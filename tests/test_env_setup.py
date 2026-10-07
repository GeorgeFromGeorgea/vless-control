import os
import tempfile
import unittest
from pathlib import Path

from vless_control.env_setup import prompt_env, validate_admin_ids, write_env


class EnvSetupTests(unittest.TestCase):
    def test_admin_ids_validation(self):
        self.assertEqual(validate_admin_ids("123,456"), "123,456")
        for invalid in ("", "abc", "123, x", "1 2"):
            with self.assertRaises(ValueError):
                validate_admin_ids(invalid)

    def test_prompt_defaults_and_never_prints_token(self):
        answers = iter(["987654321", "", "", "", ""])
        outputs = []
        values = prompt_env(input_fn=lambda _: next(answers), secret_fn=lambda _: "123456:SECRET_TOKEN", output_fn=outputs.append)
        self.assertEqual(values["DATABASE_PATH"], "data/vless-control.sqlite3")
        self.assertEqual(values["XRAY_SERVICE"], "xray")
        self.assertNotIn("SECRET_TOKEN", "".join(outputs))

    def test_write_env_mode_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_env({"TELEGRAM_BOT_TOKEN": "topsecret", "TELEGRAM_ADMIN_IDS": "123"}, Path(tmp)/".env")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("TELEGRAM_BOT_TOKEN=topsecret", path.read_text())

    def test_multiline_value_rejected(self):
        with self.assertRaises(ValueError):
            prompt_env(input_fn=lambda _: "123", secret_fn=lambda _: "token\nINJECTED=1", output_fn=lambda _: None)


if __name__ == "__main__":
    unittest.main()
