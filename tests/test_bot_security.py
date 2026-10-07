import unittest
from urllib.parse import urlparse, parse_qs

from vless_control.bot import admins_from_env
from vless_control.registry import vless_uri
from unittest.mock import patch


class SecurityAndLinkTests(unittest.TestCase):
    def test_admin_allowlist_ignores_non_numeric_entries(self):
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": "123, bad, 456"}, clear=False):
            self.assertEqual(admins_from_env(), {123, 456})
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": ""}, clear=False):
            self.assertEqual(admins_from_env(), set())

    def test_reality_uri_has_flow_and_client_params(self):
        record = {
            "label": "User", "name": "Primary", "client_uuid": "00000000-0000-4000-8000-000000000001",
            "host": "vpn.example", "port": 443, "transport": "tcp", "security": "reality",
            "sni": "www.example", "public_key": "pk", "short_id": "abcd",
        }
        uri = vless_uri(record)
        query = parse_qs(urlparse(uri).query)
        self.assertEqual(query["flow"], ["xtls-rprx-vision"])
        self.assertEqual(query["pbk"], ["pk"])
        self.assertEqual(query["sid"], ["abcd"])


if __name__ == "__main__":
    unittest.main()
