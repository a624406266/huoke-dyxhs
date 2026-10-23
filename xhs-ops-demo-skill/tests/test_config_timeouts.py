"""Timeout settings checks using memory-only configuration; no local secrets or network."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import config_loader as settings


class LlmTimeoutSettingsTests(unittest.TestCase):
    def load_memory_config(self, local):
        with patch.object(settings, "read_json", side_effect=[{}, local]) as read, patch.dict(
            settings.os.environ, {}, clear=True
        ):
            result = settings.load_config()
        self.assertEqual(read.call_count, 2)
        return result

    def save_memory_settings(self, payload, existing=None):
        sentinel = object()
        with patch.object(settings, "load_local_config", return_value=existing or {}), patch.object(
            settings, "save_local_config"
        ) as save, patch.object(settings, "load_config", return_value=sentinel):
            result = settings.update_local_settings(payload)
        self.assertIs(result, sentinel)
        save.assert_called_once()
        return save.call_args.args[0]

    def test_partial_config_gets_defaults_without_changing_model_credentials(self):
        original = {"api_base": "https://model.invalid/v1", "model": "configured-model", "api_key": "test-only"}
        config = self.load_memory_config({"llm": copy.deepcopy(original)})
        for field, default in settings.LLM_TIMEOUT_DEFAULTS.items():
            self.assertEqual(config["llm"][field], default)
        for key, value in original.items():
            self.assertEqual(config["llm"][key], value)

    def test_public_settings_include_custom_timeouts_without_api_key(self):
        config = self.load_memory_config({
            "llm": {"api_key": "test-only", "connect_timeout_s": 12, "read_timeout_s": 125, "total_timeout_s": 900}
        })
        public = settings.public_settings(config)["llm"]
        self.assertEqual([public[key] for key in settings.LLM_TIMEOUT_DEFAULTS], [12, 125, 900])
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("api_key", public)
        self.assertEqual(
            {key: settings.public_settings({"llm": {}})["llm"][key] for key in settings.LLM_TIMEOUT_DEFAULTS},
            settings.LLM_TIMEOUT_DEFAULTS,
        )

    def test_save_normalizes_integer_seconds_and_preserves_existing_model_config(self):
        original = {"api_base": "https://model.invalid/v1", "model": "configured-model", "api_key": "test-only"}
        saved = self.save_memory_settings(
            {"llm": {"connect_timeout_s": "1", "read_timeout_s": 86400, "total_timeout_s": 600.0}},
            {"llm": copy.deepcopy(original)},
        )["llm"]
        self.assertEqual([saved[key] for key in settings.LLM_TIMEOUT_DEFAULTS], [1, 86400, 600])
        for key in settings.LLM_TIMEOUT_DEFAULTS:
            self.assertIs(type(saved[key]), int)
        for key, value in original.items():
            self.assertEqual(saved[key], value)

    def test_omitted_timeout_fields_preserve_previously_saved_values(self):
        stored = {"llm": {"connect_timeout_s": 9, "read_timeout_s": 95, "total_timeout_s": 901}}
        saved = self.save_memory_settings({"llm": {"model": "updated-model"}}, stored)
        self.assertEqual({key: saved["llm"][key] for key in settings.LLM_TIMEOUT_DEFAULTS}, stored["llm"])
        self.assertEqual(saved["llm"]["model"], "updated-model")

    def test_invalid_timeout_never_saves(self):
        invalid_values = [None, "", "bad", True, False, 0, -1, 1.5, "1.5", 86401, float("nan"), float("inf"), "-Infinity"]
        for key in settings.LLM_TIMEOUT_DEFAULTS:
            for value in invalid_values:
                with self.subTest(key=key, value=value), patch.object(
                    settings, "load_local_config", return_value={}
                ), patch.object(settings, "save_local_config") as save, patch.object(settings, "load_config") as reload:
                    with self.assertRaisesRegex(ValueError, key):
                        settings.update_local_settings({"llm": {key: value}})
                    save.assert_not_called()
                    reload.assert_not_called()

    def test_invalid_stored_timeout_is_reported_without_clamping(self):
        for value in [0, 86401, 2.5, float("inf")]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "read_timeout_s"):
                self.load_memory_config({"llm": {"read_timeout_s": value}})

    def test_remote_http_model_setting_is_rejected_before_persistence(self):
        for base in ("http://model.invalid/v1", "http://localhost.attacker.invalid/v1"):
            with self.subTest(base=base), patch.object(settings, "load_local_config", return_value={}), patch.object(
                settings, "save_local_config"
            ) as save:
                with self.assertRaisesRegex(ValueError, "HTTPS"):
                    settings.update_local_settings({"llm": {"api_base": base, "api_key": "synthetic-only"}})
                save.assert_not_called()
        for base in ("", "https://model.invalid/v1", "http://localhost:9000/v1", "http://127.0.0.1:9000/v1", "http://[::1]:9000/v1"):
            with self.subTest(base=base):
                self.assertEqual(self.save_memory_settings({"llm": {"api_base": base}})["llm"]["api_base"], base)

    def test_example_and_runtime_defaults_match(self):
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
        for key, default in settings.LLM_TIMEOUT_DEFAULTS.items():
            self.assertEqual(example["llm"][key], default)
            self.assertEqual(settings.DEFAULT_CONFIG["llm"][key], default)


if __name__ == "__main__":
    unittest.main()
