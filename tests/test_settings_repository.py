from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from storage.settings_repository import SettingsRepository


class SettingsRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "settings.db"
        self.repository = SettingsRepository(self.database, seed=False)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_provider_key_is_encrypted_and_round_trips(self) -> None:
        provider_id = self.repository.save_provider({
            "name": "Test Provider",
            "api_protocol": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key": "secret-value",
            "timeout_seconds": 30,
            "enabled": True,
        })
        self.assertEqual(self.repository.get_provider(provider_id, reveal_key=True)["api_key"], "secret-value")
        db = sqlite3.connect(self.database)
        try:
            stored = db.execute("SELECT api_key_secret FROM ai_providers WHERE id=?", (provider_id,)).fetchone()[0]
        finally:
            db.close()
        self.assertNotIn("secret-value", stored)
        self.assertTrue(stored.startswith("dpapi:"))

    def test_only_one_default_model_per_type(self) -> None:
        provider_id = self.repository.save_provider({"name": "Vendor", "enabled": True})
        first = self.repository.save_model({"provider_id": provider_id, "display_name": "A", "model_id": "a", "model_type": "llm", "is_default": True})
        second = self.repository.save_model({"provider_id": provider_id, "display_name": "B", "model_id": "b", "model_type": "llm", "is_default": True})
        self.assertFalse(self.repository.get_model(first)["is_default"])
        self.assertTrue(self.repository.get_model(second)["is_default"])

    def test_provider_delete_cascades_models(self) -> None:
        provider_id = self.repository.save_provider({"name": "Vendor", "enabled": True})
        self.repository.save_model({"provider_id": provider_id, "display_name": "Vision", "model_id": "vision-a", "model_type": "vision"})
        self.repository.delete_provider(provider_id)
        self.assertEqual(self.repository.list_models(), [])

    def test_generic_settings_survive_repository_restart(self) -> None:
        value = {"daily_follow_limit": 80, "review_first_message": True}
        self.repository.set_setting("automation", value)
        reopened = SettingsRepository(self.database, seed=False)
        self.assertEqual(reopened.get_setting("automation"), value)

    def test_proxy_password_is_encrypted_and_round_trips(self) -> None:
        self.repository.save_proxy_settings({
            "enabled": True,
            "proxy_url": "http://127.0.0.1:7890",
            "username": "proxy-user",
            "password": "proxy-secret",
            "use_for_model": True,
            "use_for_internal": False,
            "verify_ssl": True,
        })
        values = self.repository.get_proxy_settings(reveal_password=True)
        self.assertEqual(values["password"], "proxy-secret")
        self.assertTrue(values["use_for_model"])
        self.assertFalse(values["use_for_internal"])
        db = sqlite3.connect(self.database)
        try:
            stored = db.execute("SELECT password_secret FROM proxy_settings WHERE id=1").fetchone()[0]
        finally:
            db.close()
        self.assertNotIn("proxy-secret", stored)


if __name__ == "__main__":
    unittest.main()
