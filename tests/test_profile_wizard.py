import unittest

from vless_control.profile_wizard import accept_value, next_field


class ProfileWizardTests(unittest.TestCase):
    def test_reality_requires_client_and_server_profile_fields(self):
        values = {}
        for field, value in (("name", "edge"), ("host", "vpn.example"), ("port", "443"),
                             ("security", "reality"), ("transport", "tcp")):
            values = accept_value(values, field, value)
        self.assertEqual(next_field(values), "sni")
        for field, value in (("sni", "www.example"), ("public_key", "public"), ("short_id", "abcd")):
            values = accept_value(values, field, value)
        self.assertIsNone(next_field(values))
        self.assertEqual(values["port"], 443)

    def test_tls_ws_requires_sni_and_path(self):
        values = {"name": "websocket", "host": "vpn.example", "port": 80, "security": "tls", "transport": "ws"}
        self.assertEqual(next_field(values), "sni")
        values = accept_value(values, "sni", "vpn.example")
        self.assertEqual(next_field(values), "path")
        with self.assertRaisesRegex(ValueError, "начинаться"):
            accept_value(values, "path", "invalid")
        values = accept_value(values, "path", "/vless")
        self.assertIsNone(next_field(values))

    def test_rejects_invalid_port_and_reality_ws(self):
        with self.assertRaisesRegex(ValueError, "числом"):
            accept_value({}, "port", "abc")
        with self.assertRaisesRegex(ValueError, "от 1 до 65535"):
            accept_value({}, "port", "70000")
        with self.assertRaisesRegex(ValueError, "только tcp"):
            accept_value({"security": "reality"}, "transport", "ws")


if __name__ == "__main__":
    unittest.main()
