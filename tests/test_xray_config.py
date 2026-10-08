import unittest

from vless_control.xray_config import build_config, build_reality_bootstrap_config, validate_config


class XrayConfigTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "edge", "port": 443, "security": "reality", "transport": "tcp",
            "sni": "www.example", "private_key": "server-private-placeholder", "short_id": "abcd",
        }

    def test_build_requires_at_least_one_profile(self):
        with self.assertRaisesRegex(ValueError, "at least one profile"):
            build_config([], [])

    def test_reality_bootstrap_config_uses_operator_parameters_and_no_clients(self):
        config = build_reality_bootstrap_config(
            name="bootstrap", port=443, sni="www.example.com",
            private_key="private-placeholder-private-placeholder", short_id="a1b2c3d4",
        )
        validate_config(config)
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["tag"], "bootstrap")
        self.assertEqual(inbound["port"], 443)
        self.assertEqual(inbound["settings"]["clients"], [])
        reality = inbound["streamSettings"]["realitySettings"]
        self.assertEqual(reality["privateKey"], "private-placeholder-private-placeholder")
        self.assertEqual(reality["serverNames"], ["www.example.com"])
        self.assertEqual(reality["shortIds"], ["a1b2c3d4"])

    def test_reality_bootstrap_rejects_invalid_operator_parameters(self):
        with self.assertRaisesRegex(ValueError, "SNI"):
            build_reality_bootstrap_config("bootstrap", 443, "", "A" * 43, "abcd")
        with self.assertRaisesRegex(ValueError, "short ID"):
            build_reality_bootstrap_config("bootstrap", 443, "www.example.com", "A" * 43, "xyz")
        with self.assertRaisesRegex(ValueError, "hostname"):
            build_reality_bootstrap_config("bootstrap", 443, "https://www.example.com", "A" * 43, "abcd")
        with self.assertRaisesRegex(ValueError, "private key"):
            build_reality_bootstrap_config("bootstrap", 443, "www.example.com", "short", "abcd")

    def test_build_reality_inbound_allows_empty_client_list_for_first_bootstrap(self):
        config = build_config([self.profile], [])
        validate_config(config)
        self.assertEqual(config["inbounds"][0]["settings"]["clients"], [])

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
