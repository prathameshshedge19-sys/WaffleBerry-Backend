"""Safe diagnostics for otherwise opaque OpenAI SDK failures."""

import logging
import unittest
from types import SimpleNamespace

from app.services.ai.openai_provider import OpenAIProvider


class OpenAIProviderDiagnosticTests(unittest.TestCase):
    def test_debug_diagnostic_contains_only_safe_metadata(self):
        provider = object.__new__(OpenAIProvider)
        provider._settings = SimpleNamespace(debug=True, ai_model=" gpt-5.5 ")
        exc = SimpleNamespace(
            status_code=404,
            code="model_not_found",
            type="invalid_request_error",
            request_id="req_safe-123",
            body={"secret": "must-not-be-logged"},
        )
        with self.assertLogs(
            "app.services.ai.openai_provider", level=logging.ERROR
        ) as captured:
            provider._log_provider_failure(exc, operation="chat_stream")
        diagnostic = captured.output[0]
        self.assertIn("OPENAI_PROVIDER_FAILURE", diagnostic)
        self.assertIn("http_status=404", diagnostic)
        self.assertIn("provider_error_code=model_not_found", diagnostic)
        self.assertIn("provider_error_type=invalid_request_error", diagnostic)
        self.assertIn("request_id=req_safe-123", diagnostic)
        self.assertIn("classification=model_or_request", diagnostic)
        self.assertIn("model=gpt-5.5 operation=chat_stream", diagnostic)
        self.assertNotIn("must-not-be-logged", diagnostic)

    def test_production_diagnostic_is_silent(self):
        provider = object.__new__(OpenAIProvider)
        provider._settings = SimpleNamespace(debug=False, ai_model="gpt-5.5")
        with self.assertNoLogs(
            "app.services.ai.openai_provider", level=logging.ERROR
        ):
            provider._log_provider_failure(
                SimpleNamespace(status_code=401), operation="chat_stream"
            )
