"""Focused tests for deployment-facing application configuration."""

import os
import unittest

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret")

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


class DeploymentConfigurationTests(unittest.TestCase):
    def test_health_endpoint_has_minimal_public_contract(self):
        response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_cors_origins_are_trimmed_and_normalized(self):
        settings = Settings(
            _env_file=None,
            jwt_secret_key="test-only-secret",
            cors_origins=" https://app.example.com/,http://localhost:4173 ",
        )

        self.assertEqual(
            settings.allowed_cors_origins,
            ["https://app.example.com", "http://localhost:4173"],
        )

    def test_wildcard_cors_origin_is_rejected(self):
        settings = Settings(
            _env_file=None,
            jwt_secret_key="test-only-secret",
            cors_origins="*",
        )

        with self.assertRaises(ValueError):
            settings.allowed_cors_origins

    def test_production_cannot_silently_use_sqlite(self):
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            Settings(
                _env_file=None,
                jwt_secret_key="test-only-secret",
                debug=False,
                database_url="sqlite:///./waffle_berry.db",
            )

    def test_development_can_explicitly_use_sqlite(self):
        settings = Settings(
            _env_file=None,
            jwt_secret_key="test-only-secret",
            debug=True,
            database_url="sqlite:///:memory:",
        )
        self.assertEqual(settings.database_url, "sqlite:///:memory:")


if __name__ == "__main__":
    unittest.main()
