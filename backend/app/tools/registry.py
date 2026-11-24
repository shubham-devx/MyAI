"""Single entry point for all assistant tools."""

from typing import Any

from app.config import settings
from app.documents import document_store
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSource, ToolSpec
from app.tools.calculator import calculate
from app.tools.csv_analysis import analyze_table, format_analysis
from app.tools.sandbox import run_sandboxed, sandbox_status
from app.tools.web_search import format_results, provider_status, search_web


class ToolRegistry:
    def __init__(self) -> None:
        self.specs: dict[str, ToolSpec] = {
            "web_search": ToolSpec(
                name="web_search",
                label="Web search",
                description=(
                    "Search the public web for current information using the operator-configured search provider. "
                    "Use this when the answer depends on recent or externally verifiable facts. Results include URLs "
                    "that must be cited."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "A focused web search query."},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    },
                    "required": ["query"],
                },
                external=True,
            ),
            "calculator": ToolSpec(
                name="calculator",
                label="Calculator",
                description=(
                    "Evaluate an arithmetic expression exactly. Supports + - * / // % **, parentheses, pi/e/tau, "
                    "and functions such as sqrt, log, ln, exp, sin, cos, tan, factorial, round, floor, ceil, min, max."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "Arithmetic expression, for example (1250 * 1.18) / 3."},
                    },
                    "required": ["expression"],
                },
            ),
            "analyze_csv": ToolSpec(
                name="analyze_csv",
                label="CSV analysis",
                description=(
                    "Compute descriptive statistics, missing values, top values, correlations, and a preview from a CSV "
                    "file uploaded to this conversation. Use the document_id shown in the CSV document list."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "The uploaded CSV document ID. Omit when exactly one CSV is attached.",
                        },
                    },
                },
            ),
            "run_code": ToolSpec(
                name="run_code",
                label="Sandboxed code execution",
                description=(
                    "Execute Python or JavaScript in an isolated container with no network access, a read-only root "
                    "filesystem, and strict CPU, memory, process, output, and time limits. The user must approve each run."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "language": {"type": "string", "enum": ["python", "javascript"]},
                        "code": {"type": "string", "description": "Complete program source code."},
                        "stdin": {"type": "string", "description": "Optional standard input for the program.", "default": ""},
                    },
                    "required": ["language", "code"],
                },
                requires_confirmation=True,
            ),
        }
        self.tool_names = tuple(self.specs)

    def schemas(self, enabled: list[str], has_csv: bool = False) -> list[dict[str, Any]]:
        names = [name for name in enabled if name in self.specs]
        return [
            self.specs[name].schema()
            for name in names
            if name != "analyze_csv" or has_csv
        ]

    def is_enabled(self, name: str, enabled: list[str], has_csv: bool = False) -> bool:
        return name in enabled and name in self.specs and (name != "analyze_csv" or has_csv)

    async def execute(self, name: str, arguments: Any, context: ToolContext) -> ToolResult:
        if not isinstance(arguments, dict):
            raise ToolError("The model supplied invalid tool arguments. They must be a JSON object.")
        if name == "web_search":
            return await self._web_search(arguments)
        if name == "calculator":
            return self._calculator(arguments)
        if name == "analyze_csv":
            return await self._analyze_csv(arguments, context)
        if name == "run_code":
            return await self._run_code(arguments)
        raise ToolError("The requested tool is not available.")

    async def _web_search(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("web_search requires a non-empty query string.")
        raw_limit = arguments.get("max_results", settings.search_max_results)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = settings.search_max_results
        limit = max(1, min(limit, 10))
        results = await search_web(query.strip(), limit)
        if not isinstance(results, list):
            results = []
        sources = [item.as_dict() for item in results]
        provider = provider_status()["label"]
        summary = f"{len(results)} result{'s' if len(results) != 1 else ''} from {provider}" if results else f"No results from {provider}"
        return ToolResult(
            content=format_results(query.strip(), results),
            summary=summary,
            data={"query": query.strip(), "provider": provider, "results": sources},
            sources=results,
        )

    def _calculator(self, arguments: dict[str, Any]) -> ToolResult:
        expression = arguments.get("expression")
        if not isinstance(expression, str):
            raise ToolError("calculator requires an expression string.")
        result = calculate(expression)
        return ToolResult(
            content=f"{result['expression']} = {result['result']}",
            summary=f"{result['expression']} = {result['result']}",
            data=result,
        )

    async def _analyze_csv(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        document_id = arguments.get("document_id")
        if document_id is not None and not isinstance(document_id, str):
            raise ToolError("analyze_csv document_id must be a string.")
        table = await document_store.get_csv_table(context.conversation_id, document_id, context.document_ids)
        analysis = analyze_table(table)
        content = format_analysis(analysis)
        insights = analysis.get("insights") or []
        summary = insights[0] if insights else f"Analyzed {table.row_count:,} rows in {table.name}."
        return ToolResult(
            content=content,
            summary=str(summary),
            data={"document": table.name, "analysis": analysis},
        )

    async def _run_code(self, arguments: dict[str, Any]) -> ToolResult:
        language = arguments.get("language")
        code = arguments.get("code")
        stdin = arguments.get("stdin", "")
        if not isinstance(language, str):
            raise ToolError("run_code requires a language.")
        if not isinstance(code, str):
            raise ToolError("run_code requires code as a string.")
        if not isinstance(stdin, str):
            stdin = ""
        execution = await run_sandboxed(language, code, stdin)
        summary = (
            f"{execution.language} exited with code {execution.exit_code} in {execution.duration_ms} ms"
            if not execution.timed_out
            else f"{execution.language} execution timed out"
        )
        return ToolResult(
            content=execution.content_for_model(),
            summary=summary,
            data=execution.as_dict(),
            status="ok" if execution.ok else "error",
        )

    async def status(self) -> dict[str, Any]:
        search = provider_status()
        sandbox = await sandbox_status()
        tools = []
        for name, spec in self.specs.items():
            if name == "web_search":
                available = bool(search["configured"])
                reason = search["hint"]
            elif name == "run_code":
                available = bool(sandbox["enabled"] and sandbox["available"])
                reason = sandbox["hint"]
            elif name == "analyze_csv":
                available = True
                reason = "Attach a CSV to the conversation to use this tool."
            else:
                available = True
                reason = None
            tools.append({
                "name": name,
                "label": spec.label,
                "description": spec.description,
                "external": spec.external,
                "requires_confirmation": spec.requires_confirmation,
                "available": available,
                "reason": reason,
            })
        return {
            "tools": tools,
            "search": search,
            "sandbox": sandbox,
            "tool_calling": {"streaming": True, "confirmation": True},
        }


tools = ToolRegistry()
