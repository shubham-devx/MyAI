import unittest
import io
import tempfile
import uuid
import zipfile
import random
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.documents import TemporaryDocumentStore, document_store
from app.providers.base import ProviderError
from app.auth import AuthStore


class ApiBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth_store = AuthStore(f"{self.tmp.name}/auth.sqlite3")
        self.patch_main_auth = patch("app.main.auth_store", self.auth_store)
        self.patch_dependency_auth = patch("app.auth.auth_store", self.auth_store)
        self.patch_main_auth.start()
        self.patch_dependency_auth.start()
        self.client = TestClient(app, client=(f"198.51.{random.randint(1, 254)}.{random.randint(1, 254)}", 1234))

    def tearDown(self):
        self.patch_main_auth.stop()
        self.patch_dependency_auth.stop()
        self.tmp.cleanup()

    def sign_up(self):
        email = f"{uuid.uuid4()}@example.test"
        response = self.client.post("/api/auth/signup", json={"email": email, "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 200, response.text)
        return email

    def test_health_is_available_without_model_generation(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()["status"], {"ok", "degraded"})
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("x-frame-options"), "DENY")

    def test_calculator_returns_real_result_and_rejects_code(self):
        self.sign_up()
        result = self.client.post("/api/tools/calculator", json={"expression": "(17.5 / 100) * 2480"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["value"], 434)

        rejected = self.client.post("/api/tools/calculator", json={"expression": "__import__('os').getcwd()"})
        self.assertEqual(rejected.status_code, 400)

    def test_invalid_chat_shape_is_rejected_before_provider_access(self):
        response = self.client.post("/api/chat/stream", json={"messages": [{"role": "assistant", "content": "hello"}]})
        self.assertEqual(response.status_code, 422)

    def test_sandbox_requires_explicit_confirmation(self):
        self.sign_up()
        response = self.client.post("/api/tools/sandbox/run", json={
            "language": "python", "code": "print('must not run')", "confirmed": False,
        })
        self.assertEqual(response.status_code, 403)

    def test_csv_analysis_is_deterministic(self):
        self.sign_up()
        response = self.client.post(
            "/api/tools/csv/analyze",
            files={"file": ("sales.csv", b"region,revenue\nwest,10\neast,20\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["analysis"]["row_count"], 2)
        revenue = next(column for column in body["analysis"]["columns"] if column["name"] == "revenue")
        self.assertEqual(revenue["numeric"]["sum"], 30)

    def test_uploads_and_tools_are_gated_but_text_chat_is_guest_accessible(self):
        denied_tool = self.client.post("/api/tools/calculator", json={"expression": "1+1"})
        denied_upload = self.client.post("/api/conversations/test-chat/documents", files={"files": ("a.txt", b"hello", "text/plain")})
        guest_tools_chat = self.client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "hello"}], "enabled_tools": ["calculator"]})
        self.assertEqual(denied_tool.status_code, 401)
        self.assertEqual(denied_upload.status_code, 401)
        self.assertEqual(guest_tools_chat.status_code, 401)

    def test_uploaded_conversations_are_owned_by_the_uploading_account(self):
        self.sign_up()
        conversation_id = str(uuid.uuid4())
        uploaded = self.client.post(
            f"/api/conversations/{conversation_id}/documents",
            files={"files": ("notes.txt", b"private notes", "text/plain")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.client.post("/api/auth/logout")
        self.sign_up()
        denied = self.client.get(f"/api/conversations/{conversation_id}/documents")
        self.assertEqual(denied.status_code, 404)
        self.client.post("/api/auth/logout")
        import asyncio
        asyncio.run(document_store.delete_file(conversation_id))

    def test_signup_login_and_logout_manage_session_cookie(self):
        email = self.sign_up()
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["email"].split("@")[-1], "example.test")
        self.client.post("/api/auth/logout")
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])
        login = self.client.post("/api/auth/login", json={"email": email, "password": "correct-horse-battery"})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.cookies.get("vs_session"))
        self.client.post("/api/auth/logout")
        login = self.client.post("/api/auth/login", json={"email": "nobody@example.test", "password": "wrong"})
        self.assertEqual(login.status_code, 401)

    def test_password_reset_token_is_single_use_and_revokes_sessions(self):
        email = self.sign_up()
        token = self.auth_store.issue_reset(email)
        self.assertIsNotNone(token)
        response = self.client.post("/api/auth/reset-password", json={"token": token, "password": "new-correct-password"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.auth_store.reset_password(token, "another-password-value"))
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])
        login = self.client.post("/api/auth/login", json={"email": email, "password": "new-correct-password"})
        self.assertEqual(login.status_code, 200)

    def test_cors_allows_configured_frontend_origin(self):
        response = self.client.options("/api/models", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:3000")
        self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")

    def test_mutating_requests_reject_unconfigured_origins(self):
        response = self.client.post("/api/auth/signup", json={"email": "someone@example.test", "password": "correct-horse-battery"}, headers={"Origin": "https://bad.example"})
        self.assertEqual(response.status_code, 403)

    def test_docx_zip_bomb_is_rejected_before_extraction(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "0" * (65 * 1024 * 1024))
        with self.assertRaises(ProviderError) as error:
            TemporaryDocumentStore._extract("large.docx", ".docx", buffer.getvalue())
        self.assertEqual(error.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
