import base64
import tempfile
import unittest
from pathlib import Path

from vless_control.registry import Registry, vless_uri


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Registry(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_individual_identity_and_multiple_fallback_profiles(self):
        valid_key = base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("=")
        p1 = self.db.add_profile("main", "vpn.example", 443, "reality", sni="www.example", public_key=valid_key, short_id="a1")
        p2 = self.db.add_profile("fallback", "vpn.example", 8443, "reality", sni="www.example", public_key=valid_key, short_id="a1")
        user = self.db.add_user("Alice")
        self.db.assign_profiles(user["id"], [p1, p2])
        links = self.db.list_connections(user["id"])
        self.assertEqual(len(links), 2)
        self.assertEqual({x["client_uuid"] for x in links}, {user["uuid"]})
        rendered = {item["port"]: vless_uri(item) for item in links}
        self.assertIn("@vpn.example:443?", rendered[443])
        self.assertIn("@vpn.example:8443?", rendered[8443])

    def test_duplicate_label_rejected_and_deactivation(self):
        user = self.db.add_user("Alice")
        with self.assertRaises(Exception):
            self.db.add_user("Alice")
        self.assertTrue(self.db.deactivate_user(user["id"]))
        self.assertFalse(self.db.deactivate_user(user["id"]))
        self.assertEqual(self.db.list_connections(user["id"]), [])

    def test_reality_requires_parameters(self):
        for sni, key, sid in (("➕ Новый ключ", "pub", "a1"), ("www.example", "🧩 Назначить профили", "a1"), ("www.example", "pub", "👤 Пользователи")):
            with self.subTest(), self.assertRaises(ValueError):
                self.db.add_profile("invalid-reality", "vpn.example", 443, "reality", sni=sni, public_key=key, short_id=sid)
        with self.assertRaises(ValueError):
            self.db.add_profile("bad", "host", 443, "reality")


if __name__ == "__main__":
    unittest.main()
