import io
import json
import unittest
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock
from hedging_tools.ai_client import AgentRouter, AIError, NoRedirect, settings


class AITests(unittest.TestCase):
    def setUp(self):
        self.config = {"AI_API_KEY": "secret-test-value", "AI_MODEL": "deepseek-v4-flash", "AI_ENABLED": "false"}

    def test_success_and_payload(self):
        response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}], "usage": {"total_tokens": 10}}
        opener = Mock(return_value=io.BytesIO(json.dumps(response).encode()))
        text, usage = AgentRouter(self.config, opener).request("Test", "Hello")
        self.assertEqual(text, "OK")
        self.assertEqual(usage["total_tokens"], 10)
        request = opener.call_args.args[0]
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertNotIn("secret-test-value", request.data.decode())
        self.assertNotIn("tools", body)
        self.assertEqual(request.full_url, "https://agentrouter.org/v1/responses")

    def test_auth_error_does_not_echo_body_or_retry(self):
        opener = Mock(side_effect=HTTPError("https://agentrouter.org/v1/responses", 401, "secret-test-value", {}, io.BytesIO(b"secret-test-value")))
        with self.assertRaises(AIError) as caught:
            AgentRouter(self.config, opener).request("Test", "Hello")
        self.assertNotIn("secret-test-value", str(caught.exception))
        self.assertIn("401", str(caught.exception))
        opener.assert_called_once()

    def test_incomplete_response(self):
        opener = Mock(return_value=io.BytesIO(b'{"status":"incomplete"}'))
        with self.assertRaises(AIError):
            AgentRouter(self.config, opener).request("Test", "Hello")

    def test_disabled_analysis(self):
        opener = Mock()
        with self.assertRaises(AIError):
            AgentRouter(self.config, opener).analyze({})
        opener.assert_not_called()

    def test_redirect_refused(self):
        with self.assertRaises(AIError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example")

    def test_apinex_chat_protocol(self):
        self.config.update(AI_BASE_URL="https://api.apinex.bond/v1", AI_MODEL="free/deepseek-v4.1-flash")
        response = {"choices": [{"finish_reason": "stop", "message": {"content": "OK"}}], "usage": {"prompt_tokens": 10}}
        opener = Mock(return_value=io.BytesIO(json.dumps(response).encode()))
        self.assertEqual(AgentRouter(self.config, opener).request("System", "Hello")[0], "OK")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.apinex.bond/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["messages"][0]["role"], "system")

    def test_old_key_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("AI_BASE_URL=https://api.apinex.bond/v1\nAI_API_STYLE=chat_completions\nAI_MODEL=free/deepseek-v4.1-flash\nAI_API_KEY=old-router-key\nAPINEX_API_KEY=\n")
            with self.assertRaises(AIError):
                settings(path)

    def test_truncated_chat_rejected(self):
        self.config["AI_BASE_URL"] = "https://api.apinex.bond/v1"
        opener = Mock(return_value=io.BytesIO(b'{"choices":[{"finish_reason":"length","message":{"content":"OK"}}]}'))
        with self.assertRaises(AIError):
            AgentRouter(self.config, opener).request("Test", "Hello")
