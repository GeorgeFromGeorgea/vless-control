import unittest

from vless_control.xray_config import build_config, validate_config


class XrayConfigTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "edge", "port": 443, "security": "reality", "transport": "tcp",
            "sni": "www.example", "private_key": "server-private-placeholder", "short_id": "abcd",
        }

    def test_build_reality_inbound(self):
        config = build_config([self.profile], [{"id": "00000000-0000-4000-8000-000000000001", "email": "test-user"}])
        validate_config(config)
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["port"], 443)
        self.assertEqual(inbound["protocol"], "vless")
        self.assertEqual(inbound["streamSettings"]["security"], "reality")
        self.assertEqual(inbound["settings"]["clients"][0]["flow"], "xtls-rprx-vision")

    def test_duplicate_ports_rejected(self):
        second = {**self.profile, "name": "other"}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_config(build_config([self.profile, second], [{"id": "id", "email": "x"}]))

    def test_reality_ws_rejected(self):
        with self.assertRaisesRegex(ValueError, "requires TCP"):
            build_config([{**self.profile, "transport": "ws"}], [{"id": "id", "email": "x"}])


if __name__ == "__main__":
    unittest.main()
