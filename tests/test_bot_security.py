import unittest
from urllib.parse import urlparse, parse_qs

from vless_control.bot import admins_from_env, button_router, STATE_KEYS
from vless_control.registry import vless_uri
from unittest.mock import patch
from types import SimpleNamespace
import asyncio


class SecurityAndLinkTests(unittest.TestCase):
    def _reply(self, replies):
        async def reply(text):
            replies.append(text)
        return reply

    def test_cancel_text_clears_every_wizard_stage_and_menu_cache(self):
        for stage in STATE_KEYS:
            with self.subTest(stage=stage):
                self._cancel_text({stage: True, "assign_users": {"1": "u"}, "assign_profiles": [], "selected_assign_user": 1, "link_users": {"1": "u"}})

    def _cancel_text(self, initial):
        data = dict(initial)
        replies = []
        message = SimpleNamespace(text="Отмена", reply_text=self._reply(replies))
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(type="private"), effective_message=message)
        context = SimpleNamespace(user_data=data)
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": "123"}):
            asyncio.run(button_router(update, context))
        self.assertEqual(data, {})
        self.assertEqual(len(replies), 1)

    def test_cancel_variants_precede_field_validation_and_menu_buttons(self):
        for text in ("Отмена", "❌ Отмена", "Отменить", "👤 Пользователи", "🔑 Профили"):
            data = {"profile_values": {}}
            replies = []
            message = SimpleNamespace(text=text, reply_text=self._reply(replies))
            update = SimpleNamespace(effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(type="private"), effective_message=message)
            with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": "123"}):
                asyncio.run(button_router(update, SimpleNamespace(user_data=data)))
            if text in {"Отмена", "❌ Отмена", "Отменить"}:
                self.assertEqual(data, {})
                self.assertEqual(replies, ["Действие отменено."])
            else:
                self.assertIn("profile_values", data)

    def test_admin_allowlist_ignores_non_numeric_entries(self):
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": "123, bad, 456"}, clear=False):
            self.assertEqual(admins_from_env(), {123, 456})
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": ""}, clear=False):
            self.assertEqual(admins_from_env(), set())

    def test_duration_button_cannot_use_legacy_boolean_state(self):
        data = {"awaiting_new_duration": True, "new_label": "1"}
        replies = []
        message = SimpleNamespace(text="1 день", reply_text=self._reply(replies))
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(type="private"), effective_message=message)
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_IDS": "123"}):
            asyncio.run(button_router(update, SimpleNamespace(user_data=data)))
        self.assertEqual(data, {})
        self.assertIn("устарел", replies[0])

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
