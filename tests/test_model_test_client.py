from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from services.model_test_client import ModelTestClient, ModelTestError


class FakeResponse:
    def __init__(self, lines=None, data=None, status_code=200) -> None:
        self.lines = lines or []
        self.data = data or {}
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(self.data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self, decode_unicode=True):
        return iter(self.lines)

    def json(self):
        return self.data


def provider(protocol="openai_compatible") -> dict:
    return {"api_key": "test-key", "enabled": True, "api_protocol": protocol, "base_url": "https://example.test/v1", "timeout_seconds": 30, "organization": ""}


def model(model_type="llm") -> dict:
    return {"model_id": "test-model", "model_type": model_type, "temperature": 0.3, "extra_json": {}, "enabled": True}


class ModelTestClientTest(unittest.TestCase):
    @patch("services.model_test_client.requests.Session.post")
    def test_openai_stream_emits_incremental_text(self, post) -> None:
        post.return_value = FakeResponse(lines=[
            'data: {"choices":[{"delta":{"content":"你"}}]}',
            'data: {"choices":[{"delta":{"content":"好"}}]}',
            "data: [DONE]",
        ])
        chunks = list(ModelTestClient().stream_test(provider(), model(), "hello", stream=True))
        self.assertEqual(chunks, ["你", "好"])
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "test-model")

    @patch("services.model_test_client.requests.Session.post")
    def test_anthropic_stream_parses_content_delta(self, post) -> None:
        post.return_value = FakeResponse(lines=[
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}',
        ])
        chunks = list(ModelTestClient().stream_test(provider("anthropic"), model(), "hello", stream=True))
        self.assertEqual(chunks, ["ok"])
        self.assertIn("x-api-key", post.call_args.kwargs["headers"])

    @patch("services.model_test_client.requests.Session.post")
    def test_embedding_returns_dimension_and_preview(self, post) -> None:
        post.return_value = FakeResponse(data={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        result = list(ModelTestClient().stream_test(provider(), model("embedding"), "hello", stream=False))[0]
        self.assertIn("向量维度：3", result)

    def test_missing_key_fails_before_network(self) -> None:
        invalid = provider()
        invalid["api_key"] = ""
        with self.assertRaisesRegex(ModelTestError, "API Key"):
            list(ModelTestClient().stream_test(invalid, model(), "hello"))


if __name__ == "__main__":
    unittest.main()
