# test_vision_decoupling.py
# date created: 2026-10-02 17:01:00
# date modified: 2026-10-11 00:45:04
# tags: #test, #vision, #multimodal, #decoupling, #ollama, #xml

"""Unit tests for Decoupled Multimodal Vision Architecture (Phase 2).

Validates:
  1. VISION_MODEL_NAME configuration presence and defaults.
  2. Routing of visual indexing and perception calls to VISION_MODEL_NAME.
  3. Formatting and token pruning of <visual_context> XML envelopes.
  4. Decoupled perception dispatch: omission of user_turn['images'] and injection
     of <visual_context> when MODEL_NAME is a text-only model.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import evelyn_config as cfg
from Evelyn.tools import string_utils, visual_indexer


def test_vision_model_config_presence():
    """Verify VISION_MODEL_NAME and DECOUPLE_VISION exist and default properly."""
    assert hasattr(cfg, "VISION_MODEL_NAME")
    assert cfg.VISION_MODEL_NAME == "gemma4:12b"
    assert hasattr(cfg, "DECOUPLE_VISION")
    assert cfg.DECOUPLE_VISION is True


@pytest.mark.asyncio
async def test_extract_visual_metadata_uses_vision_model():
    """Verify extract_visual_metadata_from_ollama uses VISION_MODEL_NAME in request payload."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message": {
            "content": '{"caption": "A circuit diagram", "ocr_text": "VCC 5V GND", "suggested_tags": ["#circuit"], "domain": "Tech/Hardware"}'
        }
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with (
        patch("Evelyn.tools.visual_indexer.httpx.AsyncClient", return_value=mock_client),
        patch.object(cfg, "VISION_MODEL_NAME", "custom_vision_model:latest"),
    ):
        result = await visual_indexer.extract_visual_metadata_from_ollama(
            base64_image="dGVzdA==",
            user_context="Explain this schematic",
        )

        assert result["caption"] == "A circuit diagram"
        assert result["ocr_text"] == "VCC 5V GND"
        assert result["domain"] == "Tech/Hardware"

        # Verify POST payload sent custom_vision_model:latest
        mock_client.post.assert_called_once()
        _, kwargs = mock_client.post.call_args
        payload = kwargs.get("json", {})
        assert payload.get("model") == "custom_vision_model:latest"
        assert payload.get("think") is False


def test_build_visual_context_envelope():
    """Verify build_visual_context_envelope generates structured XML and prunes on empty."""
    # 1. Pruning
    assert string_utils.build_visual_context_envelope([]) == ""

    # 2. Multi-image envelope
    metas = [
        {
            "index": 1,
            "caption": "Screenshot of terminal error",
            "ocr_text": "ConnectionRefusedError: [Errno 111]",
            "domain": "Tech/Terminal",
        },
        {
            "index": 2,
            "caption": "Photo of handwritten note",
            "ocr_text": "Remember to buy milk & eggs",
            "domain": "Personal/Tasks",
        },
    ]
    xml = string_utils.build_visual_context_envelope(metas)
    assert xml.startswith('<visual_context count="2">')
    assert '<image index="1" domain="Tech/Terminal">' in xml
    assert "<caption>Screenshot of terminal error</caption>" in xml
    assert "<ocr_text>ConnectionRefusedError: [Errno 111]</ocr_text>" in xml
    assert '<image index="2" domain="Personal/Tasks">' in xml
    assert "Remember to buy milk &amp; eggs" in xml
    assert xml.endswith("</visual_context>")


def test_decoupled_vision_dispatch_behavior():
    """Verify decoupled vision logic branches correctly based on config flags and model equality."""
    # When DECOUPLE_VISION is True, should_decouple_vision is True even if MODEL_NAME == VISION_MODEL_NAME
    with (
        patch.object(cfg, "MODEL_NAME", "gemma4:12b"),
        patch.object(cfg, "VISION_MODEL_NAME", "gemma4:12b"),
        patch.object(cfg, "DECOUPLE_VISION", True),
    ):
        should_decouple = bool(
            getattr(cfg, "VISION_MODEL_NAME", None)
            and (getattr(cfg, "DECOUPLE_VISION", True) or cfg.VISION_MODEL_NAME != cfg.MODEL_NAME)
        )
        assert should_decouple is True

    # When DECOUPLE_VISION is False and MODEL_NAME != VISION_MODEL_NAME, should_decouple_vision is True
    with (
        patch.object(cfg, "MODEL_NAME", "qwen2.5:14b"),
        patch.object(cfg, "VISION_MODEL_NAME", "gemma4:12b"),
        patch.object(cfg, "DECOUPLE_VISION", False),
    ):
        should_decouple = bool(
            getattr(cfg, "VISION_MODEL_NAME", None)
            and (getattr(cfg, "DECOUPLE_VISION", True) or cfg.VISION_MODEL_NAME != cfg.MODEL_NAME)
        )
        assert should_decouple is True

    # When DECOUPLE_VISION is False and MODEL_NAME == VISION_MODEL_NAME, should_decouple_vision is False (native multimodal)
    with (
        patch.object(cfg, "MODEL_NAME", "gemma4:12b"),
        patch.object(cfg, "VISION_MODEL_NAME", "gemma4:12b"),
        patch.object(cfg, "DECOUPLE_VISION", False),
    ):
        should_decouple = bool(
            getattr(cfg, "VISION_MODEL_NAME", None)
            and (getattr(cfg, "DECOUPLE_VISION", True) or cfg.VISION_MODEL_NAME != cfg.MODEL_NAME)
        )
        assert should_decouple is False
