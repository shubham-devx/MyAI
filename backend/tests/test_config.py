import unittest

from pydantic import ValidationError

from app.config import Settings


class SettingsValidationTests(unittest.TestCase):
    def test_wildcard_cors_origin_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(frontend_origins="*")

    def test_exact_frontend_origin_is_accepted(self):
        settings = Settings(frontend_origins="http://localhost:3000,https://app.example.test")
        self.assertEqual(settings.allowed_origins, ["http://localhost:3000", "https://app.example.test"])

    def test_production_requires_exact_trusted_hosts(self):
        with self.assertRaises(ValidationError):
            Settings(app_env="production")
        with self.assertRaises(ValidationError):
            Settings(app_env="production", trusted_hosts="*")
        settings = Settings(app_env="production", trusted_hosts="api.example.test,10.0.0.5")
        self.assertEqual(settings.allowed_hosts, ["api.example.test", "10.0.0.5"])


if __name__ == "__main__":
    unittest.main()
