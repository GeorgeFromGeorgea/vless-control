from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from vless_control.deploy import XrayConfigDeployer, reconcile_clients


class DeployTests(unittest.TestCase):
    def config(self):
        return {"log": {"loglevel": "warning"}, "inbounds": [
            {"tag": "edge", "protocol": "vless", "settings": {"clients": [{"id": "old"}], "decryption": "none"}},
            {"tag": "ssh", "protocol": "shadowsocks", "settings": {"clients": []}},
        ], "outbounds": [{"protocol": "freedom"}]}

    def test_reconcile_preserves_unmanaged_sections_and_replaces_managed_clients(self):
        source = self.config()
        new = reconcile_clients(source, {"edge": [{"id": "new-id", "email": "person", "flow": "xtls-rprx-vision"}]})
        self.assertEqual(new["inbounds"][0]["settings"]["clients"][0]["id"], "old")

    def test_sync_preserves_other_clients_and_removes_only_managed_entries(self):
        config = self.config()
        config["inbounds"][0]["settings"]["clients"] += [
            {"id": "managed-old", "email": "vless-control-old"},
            {"id": "manual", "email": "operator-added"},
        ]
        result = reconcile_clients(config, {"edge": [{"id": "managed-new", "email": "vless-control-new"}]})
        self.assertEqual([c["id"] for c in result["inbounds"][0]["settings"]["clients"]], ["old", "manual", "managed-new"])
        self.assertEqual(result["inbounds"][1], config["inbounds"][1])
        self.assertEqual(result["outbounds"], config["outbounds"])
        self.assertEqual(config["inbounds"][0]["settings"]["clients"][0]["id"], "old")

    def test_reconcile_rejects_unknown_or_duplicate_clients(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            reconcile_clients(self.config(), {"missing": []})
        with self.assertRaisesRegex(ValueError, "duplicate UUID"):
            reconcile_clients(self.config(), {"edge": [{"id": "x"}, {"id": "x"}]})

    def test_deploy_backup_and_rollback_on_restart_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            old = json.dumps(self.config()).encode()
            path.write_bytes(old)
            deployer = XrayConfigDeployer(path)
            calls = []
            def fake_validate(candidate):
                self.assertTrue(Path(candidate).exists())
            def fail_restart():
                calls.append("restart")
                if len(calls) == 1:
                    raise RuntimeError("service failed")
            candidate = {"log": {}, "inbounds": [], "outbounds": []}
            with patch.object(deployer, "validate", side_effect=fake_validate):
                with self.assertRaisesRegex(RuntimeError, "service failed"):
                    deployer.deploy(candidate, restart=fail_restart)
            self.assertEqual(path.read_bytes(), old)
            backups = list(Path(tmp).glob("config.json.bak.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), old)
            self.assertEqual(len(calls), 2)

    def test_refuse_missing_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            deployer = XrayConfigDeployer(Path(tmp) / "absent.json")
            with self.assertRaises(FileNotFoundError):
                deployer.deploy({})


if __name__ == "__main__":
    unittest.main()
