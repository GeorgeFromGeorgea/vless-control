import unittest
import base64
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from vless_control.profile_wizard import accept_value, next_field
from vless_control.registry import Registry, vless_uri


class ProfileWizardTests(unittest.TestCase):
    def test_reality_requires_client_and_server_profile_fields(self):
        values = {}
        for field, value in (("name", "edge"), ("host", "vpn.example"), ("port", "443"),
                             ("security", "reality"), ("transport", "tcp")):
            values = accept_value(values, field, value)
        self.assertEqual(next_field(values), "sni")
        valid_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        for field, value in (("sni", "www.example"), ("public_key", valid_key), ("short_id", "abcd")):
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

    def test_rejects_menu_text_and_malformed_reality_fields(self):
        labels = ("👤 Пользователи", "➕ Новый ключ", "🧩 Назначить профили")
        for label in labels:
            for field in ("host", "sni", "public_key", "short_id"):
                with self.subTest(label=label, field=field), self.assertRaises(ValueError):
                    accept_value({}, field, label)
        for field, value in (("host", "click menu"), ("sni", "bad host"), ("public_key", "public"), ("short_id", "xyz")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                accept_value({}, field, value)

    def test_registry_rejects_malformed_profiles(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Registry(Path(tmp.name) / "registry.sqlite3")
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        for host in ("👤 Пользователи", "➕ Новый ключ", "🧩 Назначить профили", "bad host"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                db.add_profile("p", host, 443, "reality", "tcp", "example.com", key, "abcd")
        for sni, public_key, short_id in (("👤 Пользователи", key, "abcd"), ("example.com", "➕ Новый ключ", "abcd"), ("example.com", key, "🧩 Назначить профили")):
            with self.subTest(), self.assertRaises(ValueError):
                db.add_profile("p", "example.com", 443, "reality", "tcp", sni, public_key, short_id)

    def test_reality_uri_uses_profile_host_and_encodes_parameters(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Registry(Path(tmp.name) / "registry.sqlite3")
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        pid = db.add_profile("edge", "203.0.113.7", 443, "reality", "tcp", "sni.example", key, "a1b2")
        user = db.add_user("test")
        db.assign_profiles(user["id"], [pid])
        uri = vless_uri(db.list_connections(user["id"])[0])
        parsed = urlsplit(uri)
        self.assertEqual(parsed.hostname, "203.0.113.7")
        params = parse_qs(parsed.query)
        self.assertEqual(params["security"], ["reality"])
        self.assertEqual(params["sni"], ["sni.example"])
        self.assertEqual(params["pbk"], [key])
        self.assertEqual(params["sid"], ["a1b2"])


if __name__ == "__main__":
    unittest.main()
