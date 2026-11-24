"""Small single-process API abuse controls; not a substitute for an edge proxy."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict, deque
from typing import Any
from starlette.exceptions import HTTPException


class RequestBodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body exceeds the configured size limit.")


class ApiGuardMiddleware:
    """Apply a bounded request-body cap and per-IP sliding-window request limits."""

    def __init__(
        self,
        app: Any,
        *,
        body_limit_bytes: int,
        window_seconds: int = 60,
        default_limit: int = 120,
        chat_limit: int = 12,
        upload_limit: int = 10,
        inference_concurrency: int = 2,
        clock=time.monotonic,
    ) -> None:
        self.app = app
        self.body_limit_bytes = body_limit_bytes
        self.window_seconds = window_seconds
        self.limits = (default_limit, chat_limit, upload_limit)
        self.inference_concurrency = inference_concurrency
        self._active_inference = 0
        self.clock = clock
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _limit_for(self, path: str) -> int:
        if path.startswith("/api/chat"):
            return self.limits[1]
        if path.endswith("/documents") or path.endswith("/transcribe") or path.endswith("/analyze"):
            return self.limits[2]
        return self.limits[0]

    async def _allow(self, peer: str, limit: int) -> tuple[bool, int]:
        now = self.clock()
        cutoff = now - self.window_seconds
        async with self._lock:
            bucket = self._buckets.setdefault(peer, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False, max(1, int(bucket[0] + self.window_seconds - now + 0.999))
            bucket.append(now)
            self._buckets.move_to_end(peer)
            while len(self._buckets) > 10_000:
                self._buckets.popitem(last=False)
            return True, 0

    @staticmethod
    async def _send_error(send: Any, status: int, message: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        body = json.dumps({"detail": message}).encode("utf-8")
        response_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if headers:
            response_headers.extend(headers)
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared_length = headers.get(b"content-length")
        if declared_length:
            try:
                if int(declared_length) > self.body_limit_bytes:
                    await self._send_error(send, 413, "Request body exceeds the configured size limit.")
                    return
            except ValueError:
                await self._send_error(send, 400, "Invalid Content-Length header.")
                return

        if scope.get("method") != "OPTIONS" and scope.get("path") != "/health":
            peer = scope.get("client")
            peer_ip = str(peer[0]) if peer else "unknown"
            allowed, retry_after = await self._allow(peer_ip, self._limit_for(scope.get("path", "")))
            if not allowed:
                await self._send_error(send, 429, "Too many requests. Wait before trying again.", [(b"retry-after", str(retry_after).encode())])
                return

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            event = await receive()
            if event.get("type") == "http.request":
                received += len(event.get("body", b""))
                if received > self.body_limit_bytes:
                    raise RequestBodyTooLarge()
            return event

        path = scope.get("path", "")
        is_inference = path.startswith("/api/chat") or path in {"/api/tools/code/generate", "/api/tools/structured"}
        if is_inference:
            async with self._lock:
                if self._active_inference >= self.inference_concurrency:
                    await self._send_error(send, 503, "The model service is busy. Try again shortly.", [(b"retry-after", b"2")])
                    return
                self._active_inference += 1

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._send_error(send, 413, "Request body exceeds the configured size limit.")
        finally:
            if is_inference:
                async with self._lock:
                    self._active_inference = max(0, self._active_inference - 1)
