import os
import subprocess
import tempfile
import unittest
from pathlib import Path


MOCK_INSTALLER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="${MOCK_ROOT:?}"
config="$ROOT/etc/xray/config.json"; binary="$ROOT/usr/local/bin/xray"
unit="$ROOT/etc/systemd/system/xray.service"; botunit="$ROOT/etc/systemd/system/vless-control.service"
env="$ROOT/project/.env"
fail(){ echo "$*" >&2; exit 1; }
if [[ "${EXISTING_CONFIG:-0}" == 1 ]]; then mkdir -p "$ROOT/etc/xray"; printf KEEP > "$config"; fi
for path in "$binary" "$config" "$unit" "$botunit" "$env" "$ROOT/usr/bin/xray" "$ROOT/etc/xray"; do
 [[ ! -e "$path" ]] || fail "existing detected: $path"
done
[[ "${PORT_BUSY:-0}" == 0 ]] || fail "TCP port occupied"
mkdir -p "$(dirname "$config")" "$(dirname "$binary")" "$(dirname "$unit")" "$(dirname "$env")"
printf x > "$binary.tmp"; ln "$binary.tmp" "$binary"
printf '{}' > "$config.tmp"; ln "$config.tmp" "$config"
printf unit > "$unit.tmp"; ln "$unit.tmp" "$unit"
printf bot > "$botunit.tmp"; ln "$botunit.tmp" "$botunit"
printf env > "$env.tmp"; ln "$env.tmp" "$env"
'''


class InstallerMockTests(unittest.TestCase):
    def run_mock(self, port_busy=False, existing=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "install.sh"
            script.write_text(MOCK_INSTALLER)
            env = dict(os.environ, MOCK_ROOT=str(root), PORT_BUSY="1" if port_busy else "0",
                       EXISTING_CONFIG="1" if existing else "0")
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            return result.returncode, result.stderr, {p: (root / p).exists() for p in ("etc/xray/config.json", "usr/local/bin/xray", "etc/systemd/system/xray.service", "etc/systemd/system/vless-control.service", "project/.env")},

    def test_public_host_prompt_has_clear_russian_hint(self):
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertIn("Публичный адрес VPS — IP или домен", text)
        self.assertIn("без https:// и без порта", text)
        self.assertIn("Примеры являются демонстрационными", text)

        import re
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        pattern = re.search(r"grep -Eq '([^']+)'", text).group(1)
        for unit in ("xray.service", "vless-control.service"):
            self.assertRegex(unit, pattern)

    def test_clean_mock_creates_targets(self):
        code, stderr, exists = self.run_mock()
        self.assertEqual(code, 0, stderr)
        self.assertTrue(all(exists.values()))

    def test_busy_port_fails_before_any_target_write(self):
        code, _, exists = self.run_mock(port_busy=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(any(exists.values()))

    def test_existing_config_refused_and_preserved(self):
        code, stderr, exists = self.run_mock(existing=True)
        self.assertNotEqual(code, 0, stderr)
        self.assertTrue(exists["etc/xray/config.json"])
        self.assertFalse(exists["usr/local/bin/xray"])


if __name__ == "__main__":
    unittest.main()
