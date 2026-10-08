import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vless_control.preflight import collect, render


class PreflightTests(unittest.TestCase):
    def test_supported_report_is_read_only_and_json_serializable(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text("{}")
            def fake_run(command, **kwargs):
                if command[:2] == ["ss", "-H"] or command[:3] == ["ss", "-H", "-ltnup"]:
                    return subprocess.CompletedProcess(command, 0, "LISTEN 0 128 0.0.0.0:443 0.0.0.0:* users:(('xray',pid=1))\n", "")
                return subprocess.CompletedProcess(command, 0, "inactive", "")
            with patch("vless_control.preflight._read_os_release", return_value=("Ubuntu", "24.04")), \
                 patch("vless_control.preflight.shutil.which", return_value="/usr/local/bin/xray"), \
                 patch("vless_control.preflight.os.geteuid", return_value=0), \
                 patch("vless_control.preflight.platform.machine", return_value="x86_64"), \
                 patch("vless_control.preflight.platform.python_version", return_value="3.11.0"), \
                 patch("vless_control.preflight.shutil.disk_usage", return_value=(1, 1, 10 * 1024**3)), \
                 patch("vless_control.preflight.Path.read_text", return_value="MemTotal:       4096000 kB\n"), \
                 patch("vless_control.preflight.subprocess.run", side_effect=fake_run):
                report = collect(config, runner=fake_run)
            self.assertTrue(report.supported_os)
            self.assertIn("LISTEN", report.occupied_ports["443"][0])
            self.assertIn("xray", report.services)
            self.assertIn('"supported_os": true', render(report))

    def test_unsupported_os_is_flagged(self):
        with patch("vless_control.preflight._read_os_release", return_value=("CentOS", "9")), \
             patch("vless_control.preflight.shutil.which", return_value=None), \
             patch("vless_control.preflight.shutil.disk_usage", return_value=(1, 1, 1)), \
             patch("vless_control.preflight.Path.read_text", return_value="MemTotal: 1024 kB"), \
             patch("vless_control.preflight.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            report = collect("/does/not/exist")
        self.assertFalse(report.supported_os)
        self.assertTrue(report.warnings)


if __name__ == "__main__":
    unittest.main()
