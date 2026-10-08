import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vless_control.installer import InstallPlan, backup_paths, find_binary, safe_members, verify_sha256


class InstallerScriptTests(unittest.TestCase):
    def test_script_has_syntax_and_explicit_non_autostart(self):
        import subprocess
        script = Path(__file__).resolve().parents[1] / "install.sh"
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = script.read_text()
        self.assertIn("systemctl daemon-reload", text)
        import re
        self.assertIn('systemctl enable --now "$XRAY_SERVICE"', text)
        self.assertIn('"$XRAY_BINARY"', text)
        self.assertIn('"$XRAY_CONFIG_PATH"', text)
        self.assertIn("XRAY_SERVICE}.service", text)
        self.assertIn("sha256sum --check", text)

    def test_first_install_includes_config_and_requires_explicit_start_confirmation(self):
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertIn("build_reality_bootstrap_config", text)
        self.assertIn("run -test -config", text)
        self.assertIn('"$XRAY_CONFIG_PATH"', text)
        self.assertIn('"$XRAY_BINARY"', text)
        self.assertIn('systemctl enable --now', text)

    def test_installer_configures_reality_and_preserves_existing_env(self):
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        for required in ("read -rsp", "x25519", "openssl rand", "run -test -config",
                         "build_reality_bootstrap_config", "backup_dir", "cmp -s",
                         "ss -H -lnt", "XRAY_CONFIG_PATH"):
            self.assertIn(required, text)
        self.assertIn("No VLESS client is created", text)
        self.assertIn('DEFAULT_REALITY_SNI="www.cloudflare.com"', text)
        self.assertIn('REALITY_SNI=${REALITY_SNI:-$DEFAULT_REALITY_SNI}', text)
        self.assertIn("без https:// и пути", text)
        self.assertNotIn('write_env(prompt_env(), ".env")', text)

    def test_installer_parses_actual_xray_public_key_label(self):
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertIn("Password", text)
        self.assertIn("SHORT_ID", text)

        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertIn('"$XRAY_UNIT"', text)
        self.assertIn("vless-control.service", text)
        self.assertIn("vless-control-bot", text)


class InstallerTests(unittest.TestCase):
    def test_plan_refuses_existing_xray_without_explicit_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "xray.tar.gz"
            artifact.write_bytes(b"artifact")
            plan = InstallPlan("Ubuntu", "24.04", "x86_64", artifact,
                Path(tmp)/"xray", Path(tmp)/"config.json", Path(tmp)/"xray.service",
                True, False, False, False)
            with self.assertRaisesRegex(RuntimeError, "уже найден"):
                plan.validate()

    def test_plan_rejects_unsupported_os_and_requires_backup_for_replacement(self):
        dummy = Path("/tmp/artifact")
        plan = InstallPlan("CentOS", "9", "x86_64", dummy, Path("/tmp/xray"),
            Path("/tmp/config"), Path("/tmp/unit"), False, False, False, False)
        with self.assertRaisesRegex(RuntimeError, "Неподдерживаемая"):
            plan.validate()
        plan = InstallPlan("Ubuntu", "24.04", "x86_64", dummy, Path("/tmp/xray"),
            Path("/tmp/config"), Path("/tmp/unit"), True, True, True, False)
        plan.artifact.parent.mkdir(parents=True, exist_ok=True)
        plan.artifact.write_bytes(b"test artifact")
        with self.assertRaisesRegex(RuntimeError, "backup"):
            plan.validate()

    def test_checksum_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "artifact"
            file.write_bytes(b"xray")
            expected = hashlib.sha256(b"other").hexdigest()
            with self.assertRaisesRegex(ValueError, "mismatch"):
                verify_sha256(file, expected)

    def test_archive_path_traversal_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "bad.tar"
            with tarfile.open(archive_path, "w") as tf:
                data = b"bad"
                item = tarfile.TarInfo("../outside")
                item.size = len(data)
                tf.addfile(item, io.BytesIO(data))
            dest = root / "dest"
            dest.mkdir()
            with tarfile.open(archive_path) as tf:
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    safe_members(tf, dest)

    def test_backup_is_created_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "xray"
            file.write_text("binary")
            backup = backup_paths([file], root / "backups")
            with tarfile.open(backup, "r:gz") as tf:
                self.assertEqual(len(tf.getmembers()), 1)
                self.assertTrue(tf.getmembers()[0].name.endswith("/xray"))


if __name__ == "__main__":
    unittest.main()
