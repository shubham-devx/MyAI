import unittest
import asyncio

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import httpx

from app.middleware import ApiGuardMiddleware


class ApiGuardTests(unittest.TestCase):
    def test_rate_limit_returns_retry_after(self):
        app = FastAPI()
        app.add_middleware(
            ApiGuardMiddleware,
            body_limit_bytes=1024,
            window_seconds=30,
            default_limit=1,
            chat_limit=1,
            upload_limit=1,
        )

        @app.get("/limited")
        async def limited():
            return {"ok": True}

        client = TestClient(app)
        self.assertEqual(client.get("/limited").status_code, 200)
        limited_response = client.get("/limited")
        self.assertEqual(limited_response.status_code, 429)
        self.assertGreaterEqual(int(limited_response.headers["retry-after"]), 1)

    def test_auth_routes_use_stricter_separate_ip_limits(self):
        app = FastAPI()
        app.add_middleware(
            ApiGuardMiddleware,
            body_limit_bytes=1024,
            window_seconds=60,
            default_limit=20,
            auth_limit=2,
            recovery_limit=1,
        )

        @app.post("/api/auth/login")
        async def login():
            return {"ok": True}

        @app.post("/api/auth/forgot-password")
        async def forgot_password():
            return {"ok": True}

        client = TestClient(app)
        self.assertEqual(client.post("/api/auth/login").status_code, 200)
        self.assertEqual(client.post("/api/auth/login").status_code, 200)
        limited = client.post("/api/auth/login")
        self.assertEqual(limited.status_code, 429)
        self.assertGreaterEqual(int(limited.headers["retry-after"]), 1)
        # A login burst must not consume the independent password-recovery bucket.
        self.assertEqual(client.post("/api/auth/forgot-password").status_code, 200)
        self.assertEqual(client.post("/api/auth/forgot-password").status_code, 429)

    def test_declared_oversized_body_is_rejected_before_endpoint(self):
        app = FastAPI()
        app.add_middleware(ApiGuardMiddleware, body_limit_bytes=4)
        reached_endpoint = False

        @app.post("/body")
        async def body(request: Request):
            nonlocal reached_endpoint
            reached_endpoint = True
            return {"length": len(await request.body())}

        response = TestClient(app).post("/body", content=b"12345")
        self.assertEqual(response.status_code, 413)
        self.assertFalse(reached_endpoint)


class ApiGuardConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_excess_chat_generation_is_rejected_instead_of_queued(self):
        app = FastAPI()
        app.add_middleware(ApiGuardMiddleware, body_limit_bytes=1024, inference_concurrency=1)
        entered = asyncio.Event()
        release = asyncio.Event()

        @app.post("/api/chat/test")
        @app.post("/api/tools/code/generate")
        async def chat():
            entered.set()
            await release.wait()
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_request = asyncio.create_task(client.post("/api/chat/test", json={}))
            await entered.wait()
            second = await client.post("/api/tools/code/generate", json={})
            release.set()
            first = await first_request

        self.assertEqual(second.status_code, 503)
        self.assertEqual(first.status_code, 200)

    async def test_unknown_length_body_is_also_capped(self):
        app = FastAPI()
        app.add_middleware(ApiGuardMiddleware, body_limit_bytes=4)

        @app.post("/body")
        async def body(request: Request):
            return {"length": len(await request.body())}

        class ChunkedBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"123"
                yield b"456"

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/body", content=ChunkedBody())
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
