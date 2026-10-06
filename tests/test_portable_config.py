"""Offline checks for a user's own portal origin and first-run configuration."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from server import make_app, normalize_entry_origin
from chatgpt_image_ui import load_config
from link_sync import config as sync_config


class PortableConfigTests(unittest.TestCase):
    def test_origin_normalization_and_local_preview(self):
        self.assertEqual(normalize_entry_origin("https://ENTRY.example.com:443/"), "https://entry.example.com")
        self.assertEqual(normalize_entry_origin("http://localhost:8791/"), "http://localhost:8791")
        self.assertEqual(normalize_entry_origin(""), "")
        for value in ["javascript:alert(1)", "http://public.example.com", "https://a.example/path",
                      "https://user:pass@a.example", "https://a.example?q=1", "https://a.example#x"]:
            with self.assertRaises(ValueError):
                normalize_entry_origin(value)

    def test_own_origin_is_injected_and_explicit_value_overrides_environment(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"FABRIC_ENTRY_ORIGIN":"https://env.example.com"}):
            for configured, expected in [(None, "https://env.example.com"), ("https://own.example.com", "https://own.example.com"), ("", "")]:
                app = make_app(config_path=Path(folder)/"config.json", validate_startup=False, entry_origin=configured)
                try:
                    html = app.test_client().get("/").get_data(as_text=True)
                    self.assertIn("const entryOrigin = " + json.dumps(expected) + ";", html)
                finally:
                    app.extensions["job_manager"].close()

    def test_examples_contain_no_saved_coordinates_and_token_is_not_usable(self):
        cfg = load_config(ROOT / "app/config.example.json")
        self.assertEqual(cfg["points"], {})
        self.assertEqual(cfg["launcher"], "")
        with self.assertRaises(ValueError):
            sync_config(ROOT / "app/link_sync_config.example.json")


if __name__ == "__main__":
    unittest.main()
