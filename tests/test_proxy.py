from __future__ import annotations

import unittest

from services.proxy import build_proxy_map, request_verify_ssl


class ProxyResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = {
            "enabled": True,
            "proxy_url": "http://127.0.0.1:7890",
            "username": "user@example.com",
            "password": "p@ss word",
            "use_for_model": True,
            "use_for_internal": False,
            "bypass_hosts": "localhost,.internal.test",
            "verify_ssl": False,
        }

    def test_authenticated_proxy_url_is_safely_encoded(self) -> None:
        proxies = build_proxy_map(self.settings, "https://api.example.com/v1", "model")
        self.assertEqual(proxies["https"], "http://user%40example.com:p%40ss%20word@127.0.0.1:7890")

    def test_scope_and_bypass_disable_proxy(self) -> None:
        self.assertIsNone(build_proxy_map(self.settings, "https://api.example.com", "internal"))
        self.assertIsNone(build_proxy_map(self.settings, "https://service.internal.test/v1", "model"))

    def test_ssl_setting_only_applies_to_enabled_scope(self) -> None:
        self.assertFalse(request_verify_ssl(self.settings, "model"))
        self.assertTrue(request_verify_ssl(self.settings, "internal"))


if __name__ == "__main__":
    unittest.main()
