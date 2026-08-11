# test_image_generation.py
# date created: 2026-08-11
# tags: #test, #image, #flux, #unit-test

import sys
import os
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add project root and tools directory to sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOLS_DIR = os.path.join(BASE_DIR, "Evelyn", "tools")
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import evelyn_tools


class TestImageGenerationTool(unittest.TestCase):
    def setUp(self):
        self.mock_image_dir = "/tmp/test_evelyn_image_output"
        os.makedirs(self.mock_image_dir, exist_ok=True)

    @patch("requests.post")
    @patch("requests.get")
    @patch("evelyn_config.IMAGE_SERVER_URL", "http://ricky-pc.tail0e161b.ts.net:5055")
    @patch("evelyn_config.IMAGE_OUTPUT_DIR", "/tmp/test_evelyn_image_output")
    def test_generate_image_success_with_remote_cache(self, mock_get, mock_post):
        # Mock POST /generate response from remote image server
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "filename": "image_20260811_test_forest.png",
            "url": "/images/image_20260811_test_forest.png",
            "elapsed_seconds": 2.5,
            "seed": 42,
            "aspect_ratio": "16:9"
        }
        mock_post.return_value = mock_post_resp

        # Mock GET /images/... download response
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.content = b"fake_png_binary_data"
        mock_get.return_value = mock_get_resp

        result = evelyn_tools.generate_image(
            prompt="A serene misty forest",
            aspect_ratio="16:9",
            seed=42,
            short_title="test_forest"
        )

        self.assertIn("Image generated successfully at /images/image_20260811_test_forest.png.", result)
        mock_post.assert_called_once_with(
            "http://ricky-pc.tail0e161b.ts.net:5055/generate",
            json={
                "prompt": "A serene misty forest",
                "aspect_ratio": "16:9",
                "short_title": "test_forest",
                "seed": 42
            },
            timeout=600
        )
        mock_get.assert_called_once_with(
            "http://ricky-pc.tail0e161b.ts.net:5055/images/image_20260811_test_forest.png",
            timeout=30
        )

        # Verify binary file was cached locally
        cached_path = Path(self.mock_image_dir) / "image_20260811_test_forest.png"
        self.assertTrue(cached_path.exists())
        with open(cached_path, "rb") as f:
            self.assertEqual(f.read(), b"fake_png_binary_data")

        # Cleanup cached test file
        if cached_path.exists():
            cached_path.unlink()

    @patch("requests.post")
    @patch("evelyn_config.IMAGE_SERVER_URL", "http://ricky-pc.tail0e161b.ts.net:5055")
    @patch("evelyn_config.IMAGE_OUTPUT_DIR", "/tmp/test_evelyn_image_output")
    def test_generate_image_parameter_aliases(self, mock_post):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "filename": "image_alias_test.png",
            "url": "/images/image_alias_test.png"
        }
        mock_post.return_value = mock_post_resp

        with patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 404

            result = evelyn_tools.generate_image(
                description="Futuristic cyberpunk neon skyline",
                aspect_ratio="1:1",
                title="cyberpunk_city"
            )

            self.assertIn("Image generated successfully at /images/image_alias_test.png.", result)
            mock_post.assert_called_once_with(
                "http://ricky-pc.tail0e161b.ts.net:5055/generate",
                json={
                    "prompt": "Futuristic cyberpunk neon skyline",
                    "aspect_ratio": "1:1",
                    "short_title": "cyberpunk_city"
                },
                timeout=600
            )

    def test_generate_image_empty_prompt(self):
        result = evelyn_tools.generate_image(prompt="")
        self.assertIn("Error: generate_image called with an empty prompt", result)

    @patch("requests.post")
    @patch("evelyn_config.IMAGE_SERVER_URL", "http://ricky-pc.tail0e161b.ts.net:5055")
    @patch("evelyn_config.IMAGE_OUTPUT_DIR", "/tmp/test_evelyn_image_output")
    def test_generate_image_server_error(self, mock_post):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 500
        mock_post_resp.text = "Internal CUDA Error"
        mock_post.return_value = mock_post_resp

        result = evelyn_tools.generate_image(prompt="Valid prompt")
        self.assertIn("Error from Image Engine: Internal CUDA Error", result)

    @patch("requests.post", side_effect=Exception("Connection refused"))
    @patch("evelyn_config.IMAGE_SERVER_URL", "http://ricky-pc.tail0e161b.ts.net:5055")
    @patch("evelyn_config.IMAGE_OUTPUT_DIR", "/tmp/test_evelyn_image_output")
    def test_generate_image_connection_failure(self, mock_post):
        result = evelyn_tools.generate_image(prompt="Valid prompt")
        self.assertIn("Failed to generate image via FLUX.1 server at http://ricky-pc.tail0e161b.ts.net:5055", result)


if __name__ == "__main__":
    unittest.main()
