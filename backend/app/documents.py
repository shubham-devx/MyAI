import asyncio
import csv
import io
import math
import re
import time
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath

from docx import Document as DocxDocument
from fastapi import UploadFile
from pypdf import PdfReader

from app.providers.base import ProviderError
from app.tools.base import ToolError
from app.tools.csv_analysis import CsvTable, parse_csv

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 8
MAX_FILES_PER_CONVERSATION = 8
MAX_EXTRACTED_CHARS = 2_000_000
MAX_DOCX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_CONVERSATION_CHARS = 8_000_000
MAX_PROCESS_CHARS = 32_000_000
CHUNK_CHARS = 3_500
CHUNK_OVERLAP = 350
DOCUMENT_TTL_SECONDS = 4 * 60 * 60
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv"}


@dataclass
class TextChunk:
    index: int
    text: str


@dataclass
class StoredDocument:
    id: str
    name: str
    extension: str
    size: int
    chunks: list[TextChunk]
    csv_table: CsvTable | None = None


@dataclass
class ConversationDocuments:
    documents: dict[str, StoredDocument] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)


class TemporaryDocumentStore:
    """Per-process document cache. No document bytes or extracted text reach a database or disk."""

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationDocuments] = {}
        self._lock = asyncio.Lock()

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, value in self._conversations.items() if now - value.updated_at > DOCUMENT_TTL_SECONDS]
        for key in expired:
            self._conversations.pop(key, None)

    async def add_files(self, conversation_id: str, uploads: list[UploadFile]) -> list[dict[str, object]]:
        if not conversation_id.strip() or len(conversation_id) > 64:
            raise ProviderError("Invalid conversation identifier.", 422)
        if not uploads or len(uploads) > MAX_FILES_PER_UPLOAD:
            raise ProviderError(f"Upload between 1 and {MAX_FILES_PER_UPLOAD} files at a time.", 422)

        parsed: list[StoredDocument] = []
        total_upload_bytes = 0
        for upload in uploads:
            name = PurePath((upload.filename or "").replace("\\", "/")).name
            extension = PurePath(name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ProviderError(f"{name or 'This file'} has an unsupported format. Use PDF, DOCX, TXT, or CSV.", 415)
            raw = await upload.read(MAX_FILE_BYTES + 1)
            total_upload_bytes += len(raw)
            if total_upload_bytes > 40 * 1024 * 1024:
                raise ProviderError("The combined upload limit is 40 MB per request.", 413)
            if not raw:
                raise ProviderError(f"{name} is empty.", 422)
            if len(raw) > MAX_FILE_BYTES:
                raise ProviderError(f"{name} exceeds the 15 MB upload limit.", 413)
            text = self._extract(name, extension, raw)
            if len(text) > MAX_EXTRACTED_CHARS:
                text = text[:MAX_EXTRACTED_CHARS]
            if not text.strip():
                raise ProviderError(f"No readable text was found in {name}.", 422)
            csv_table: CsvTable | None = None
            if extension == ".csv":
                try:
                    csv_table = parse_csv(raw.decode("utf-8-sig"), name)
                except UnicodeDecodeError as error:
                    raise ProviderError(f"{name} must be UTF-8 encoded CSV.", 422) from error
                except ToolError as error:
                    raise ProviderError(f"{name}: {error.message}", 422) from error
            parsed.append(StoredDocument(
                id=str(uuid.uuid4()),
                name=name,
                extension=extension,
                size=len(raw),
                chunks=self._chunk(text),
                csv_table=csv_table,
            ))

        async with self._lock:
            self._purge_expired()
            conversation = self._conversations.get(conversation_id)
            existing_documents = conversation.documents if conversation else {}
            existing_chars = sum(len(chunk.text) for document in existing_documents.values() for chunk in document.chunks)
            process_chars = sum(len(chunk.text) for stored in self._conversations.values() for document in stored.documents.values() for chunk in document.chunks)
            incoming_chars = sum(len(chunk.text) for document in parsed for chunk in document.chunks)
            if len(existing_documents) + len(parsed) > MAX_FILES_PER_CONVERSATION:
                raise ProviderError(f"A conversation can have up to {MAX_FILES_PER_CONVERSATION} uploaded files.", 413)
            if existing_chars + incoming_chars > MAX_CONVERSATION_CHARS or process_chars + incoming_chars > MAX_PROCESS_CHARS:
                raise ProviderError("The temporary document-processing memory limit has been reached. Remove files or start a new conversation.", 413)
            conversation = conversation or self._conversations.setdefault(conversation_id, ConversationDocuments())
            for document in parsed:
                conversation.documents[document.id] = document
            conversation.updated_at = time.monotonic()
            return [self._metadata(document) for document in conversation.documents.values()]

    async def list_files(self, conversation_id: str) -> list[dict[str, object]]:
        async with self._lock:
            self._purge_expired()
            conversation = self._conversations.get(conversation_id)
            if not conversation:
                return []
            conversation.updated_at = time.monotonic()
            return [self._metadata(document) for document in conversation.documents.values()]

    async def delete_file(self, conversation_id: str, document_id: str | None = None) -> None:
        async with self._lock:
            self._purge_expired()
            if document_id is None:
                self._conversations.pop(conversation_id, None)
                return
            conversation = self._conversations.get(conversation_id)
            if conversation:
                conversation.documents.pop(document_id, None)
                conversation.updated_at = time.monotonic()
                if not conversation.documents:
                    self._conversations.pop(conversation_id, None)

    async def clear_all(self) -> None:
        async with self._lock:
            self._conversations.clear()

    async def csv_documents(self, conversation_id: str | None) -> list[dict[str, object]]:
        if not conversation_id:
            return []
        async with self._lock:
            self._purge_expired()
            conversation = self._conversations.get(conversation_id)
            if not conversation:
                return []
            conversation.updated_at = time.monotonic()
            return [
                {"id": document.id, "name": document.name, "rows": document.csv_table.row_count, "columns": document.csv_table.column_count}
                for document in conversation.documents.values()
                if document.csv_table is not None
            ]

    async def get_csv_table(
        self,
        conversation_id: str | None,
        document_id: str | None = None,
        available_ids: list[str] | None = None,
    ) -> CsvTable:
        if not conversation_id:
            raise ToolError("CSV analysis requires a conversation with an attached CSV file.", 404)
        async with self._lock:
            self._purge_expired()
            conversation = self._conversations.get(conversation_id)
            if not conversation:
                raise ToolError("No attached CSV is available in this conversation. Re-upload the file.", 404)
            csv_documents = [document for document in conversation.documents.values() if document.csv_table is not None]
            conversation.updated_at = time.monotonic()
        if document_id:
            document = conversation.documents.get(document_id)
            if document is None or document.csv_table is None:
                raise ToolError("The requested CSV document is not available in this conversation.", 404)
            return document.csv_table
        candidates = [document for document in csv_documents if not available_ids or document.id in available_ids]
        if not candidates and available_ids:
            candidates = csv_documents
        if not candidates:
            raise ToolError("Attach a CSV file to this conversation before asking for data analysis.", 404)
        if len(candidates) > 1:
            names = ", ".join(f"{document.name} ({document.id})" for document in candidates[:5])
            raise ToolError(f"Multiple CSV files are attached. Specify a document_id: {names}.")
        return candidates[0].csv_table

    async def retrieve(self, conversation_id: str | None, query: str, limit: int = 5) -> list[tuple[str, int, str]]:
        if not conversation_id:
            return []
        async with self._lock:
            self._purge_expired()
            conversation = self._conversations.get(conversation_id)
            if not conversation:
                return []
            conversation.updated_at = time.monotonic()
            chunks = [(document.name, chunk.index, chunk.text) for document in conversation.documents.values() for chunk in document.chunks]
        query_terms = {term for term in re.findall(r"[\w-]{2,}", query.lower()) if term not in _STOP_WORDS}
        if not query_terms:
            return []
        tokenized = [re.findall(r"[\w-]{2,}", text.lower()) for _, _, text in chunks]
        document_frequency = Counter(term for terms in tokenized for term in set(terms))
        total = max(1, len(chunks))
        average_length = sum(map(len, tokenized)) / total or 1
        ranked: list[tuple[float, tuple[str, int, str]]] = []
        for item, terms in zip(chunks, tokenized):
            frequency = Counter(terms)
            score = 0.0
            for term in query_terms:
                if term not in frequency:
                    continue
                inverse = (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
                inverse = max(0.05, math.log1p(inverse))
                term_frequency = frequency[term]
                score += inverse * (term_frequency * 2.2) / (term_frequency + 1.2 * (0.25 + 0.75 * len(terms) / average_length))
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    @staticmethod
    def _metadata(document: StoredDocument) -> dict[str, object]:
        metadata: dict[str, object] = {
            "id": document.id,
            "name": document.name,
            "type": document.extension[1:],
            "size": document.size,
            "chunks": len(document.chunks),
        }
        if document.csv_table is not None:
            metadata["rows"] = document.csv_table.row_count
            metadata["columns"] = document.csv_table.column_count
            metadata["truncated"] = document.csv_table.truncated
        return metadata

    @staticmethod
    def _extract(name: str, extension: str, raw: bytes) -> str:
        try:
            if extension == ".txt":
                return raw.decode("utf-8-sig")
            if extension == ".csv":
                source = raw.decode("utf-8-sig")
                rows = csv.reader(io.StringIO(source))
                return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
            if extension == ".pdf":
                if not raw.startswith(b"%PDF-"):
                    raise ProviderError(f"{name} is not a valid PDF file.", 422)
                reader = PdfReader(io.BytesIO(raw), strict=True)
                parts: list[str] = []
                for page in reader.pages:
                    parts.append(page.extract_text() or "")
                    if sum(map(len, parts)) >= MAX_EXTRACTED_CHARS:
                        break
                return "\n".join(parts)
            if extension == ".docx":
                if not zipfile.is_zipfile(io.BytesIO(raw)):
                    raise ProviderError(f"{name} is not a valid DOCX file.", 422)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    entries = archive.infolist()
                    if sum(entry.file_size for entry in entries) > MAX_DOCX_EXPANDED_BYTES:
                        raise ProviderError(f"{name} expands beyond the 64 MB document-processing limit.", 413)
                    if any(entry.file_size > MAX_DOCX_EXPANDED_BYTES for entry in entries):
                        raise ProviderError(f"{name} contains an oversized compressed document entry.", 413)
                    if "word/document.xml" not in {entry.filename for entry in entries}:
                        raise ProviderError(f"{name} is not a valid DOCX document.", 422)
                document = DocxDocument(io.BytesIO(raw))
                paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
                tables = [" | ".join(cell.text.strip() for cell in row.cells) for table in document.tables for row in table.rows]
                return "\n".join(paragraphs + tables)
        except ProviderError:
            raise
        except (UnicodeDecodeError, csv.Error, zipfile.BadZipFile, ValueError, OSError) as error:
            raise ProviderError(f"{name} is corrupted or could not be read.", 422) from error
        except Exception as error:
            raise ProviderError(f"{name} is corrupted or could not be read.", 422) from error
        raise ProviderError(f"{name} has an unsupported format.", 415)

    @staticmethod
    def _chunk(text: str) -> list[TextChunk]:
        normalized = re.sub(r"\s+", " ", text).strip()
        chunks: list[TextChunk] = []
        start = 0
        while start < len(normalized):
            end = min(start + CHUNK_CHARS, len(normalized))
            if end < len(normalized):
                boundary = normalized.rfind(" ", start + CHUNK_CHARS // 2, end)
                if boundary > start:
                    end = boundary
            part = normalized[start:end].strip()
            if part:
                chunks.append(TextChunk(index=len(chunks) + 1, text=part))
            if end >= len(normalized):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)
        return chunks


_STOP_WORDS = {"the", "and", "for", "are", "but", "not", "with", "from", "that", "this", "what", "when", "where", "which", "about", "into", "have", "does", "how"}
document_store = TemporaryDocumentStore()
