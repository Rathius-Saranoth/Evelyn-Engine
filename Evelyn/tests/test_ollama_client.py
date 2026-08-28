# test_ollama_client.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #tests, #ollama_client, #http

"""Unit tests for Evelyn.tools.ollama_client."""

import json
from unittest.mock import MagicMock, patch

from Evelyn.tools.ollama_client import (
    get_ollama_status,
    query_ollama,
    query_ollama_json,
)


class TestOllamaClient:
    @patch("urllib.request.urlopen")
    def test_query_ollama_chat_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "<think>Thinking...</think>Answer here"}
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = query_ollama("Test prompt", strip_thinking=True)
        assert result == "Answer here"

    @patch("urllib.request.urlopen")
    def test_query_ollama_generate_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": "Generated text output"
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = query_ollama("Test prompt", endpoint="/api/generate")
        assert result == "Generated text output"

    @patch("urllib.request.urlopen")
    def test_query_ollama_json_with_code_fences(self, mock_urlopen):
        mock_resp = MagicMock()
        raw_llm = "Here is your JSON:\n```json\n{\"tags\": [\"alpha\", \"beta\"], \"status\": \"ok\"}\n```\nHope that helps!"
        mock_resp.read.return_value = json.dumps({
            "message": {"content": raw_llm}
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = query_ollama_json("Extract tags")
        assert result == {"tags": ["alpha", "beta"], "status": "ok"}

    @patch("urllib.request.urlopen")
    def test_get_ollama_status(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "gemma4:12b"}, {"name": "nomic-embed-text:latest"}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        status = get_ollama_status()
        assert status["status"] == "online"
        assert "gemma4:12b" in status["models"]
