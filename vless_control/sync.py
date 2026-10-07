"""CLI for syncing active registry UUIDs into an existing Xray config."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from .deploy import XrayConfigDeployer, reconcile_clients
from .registry import Registry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write config, make backup, validate, and restart service")
    parser.add_argument("--no-restart", action="store_true", help="apply file but do not restart service (advanced)")
    args = parser.parse_args()
    config_path = Path(os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"))
    xray_bin = os.getenv("XRAY_BINARY", "/usr/local/bin/xray")
    db_path = os.getenv("DATABASE_PATH", "data/vless-control.sqlite3")
    if not config_path.is_file():
        raise SystemExit(f"Existing Xray config not found: {config_path}; refusing to create a new host config")
    try:
        original = json.loads(config_path.read_text(encoding="utf-8"))
        assignments = Registry(db_path).xray_assignments()
        candidate = reconcile_clients(original, assignments)
        rendered = json.dumps(candidate, indent=2, ensure_ascii=False) + "\n"
        if not args.apply:
            print(rendered)
            print("DRY RUN only. Review output; pass --apply to write with backup, validate and restart.")
            return
        deployer = XrayConfigDeployer(config_path, xray_bin)
        restart = None
        if not args.no_restart:
            unit = os.getenv("XRAY_SERVICE", "xray")
            restart = lambda: subprocess.run(["systemctl", "restart", unit], check=True, timeout=60)
        backup = deployer.deploy(candidate, restart=restart)
        print(f"Applied; backup: {backup}")
    except Exception as exc:
        raise SystemExit(f"Sync failed ({type(exc).__name__}): {exc}") from exc


if __name__ == "__main__":
    main()
