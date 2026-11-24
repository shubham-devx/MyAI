"""In-memory approval broker for consequential tool calls."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingConfirmation:
    id: str
    tool: str
    arguments: Any
    future: asyncio.Future[bool]
    created_at: float = field(default_factory=time.monotonic)


class ConfirmationBroker:
    """Holds short-lived approvals while an SSE response waits for the user.

    Approvals are process-local and disappear on backend restart. A confirmation ID is
    random and is only used to resolve the one waiting tool call.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}

    def begin(self, tool: str, arguments: Any) -> tuple[str, asyncio.Future[bool]]:
        loop = asyncio.get_running_loop()
        confirmation_id = uuid.uuid4().hex
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[confirmation_id] = PendingConfirmation(
            id=confirmation_id,
            tool=tool,
            arguments=arguments,
            future=future,
        )
        return confirmation_id, future

    def resolve(self, confirmation_id: str, approved: bool) -> bool:
        pending = self._pending.get(confirmation_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(bool(approved))
        return True

    def discard(self, confirmation_id: str) -> None:
        pending = self._pending.pop(confirmation_id, None)
        if pending is not None and not pending.future.done():
            pending.future.cancel()

    async def wait(
        self,
        confirmation_id: str,
        future: asyncio.Future[bool],
        timeout: float,
        is_disconnected=None,
    ) -> bool | None:
        """Wait for approval. Returns True/False, or None for timeout/disconnect."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                if future.done():
                    return bool(future.result()) if not future.cancelled() else None
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                if is_disconnected is not None:
                    try:
                        if await is_disconnected():
                            return None
                    except Exception:
                        pass
                try:
                    return bool(await asyncio.wait_for(asyncio.shield(future), min(0.5, remaining)))
                except asyncio.TimeoutError:
                    continue
        finally:
            self._pending.pop(confirmation_id, None)


confirmation_broker = ConfirmationBroker()
