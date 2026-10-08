"""Safe first-run REALITY config generation and validation CLI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .xray_config import build_reality_bootstrap_config, validate_config


def run(options: dict) -> int:
    target = Path(options["config"])
    if options.get("apply") and target.exists():
        raise FileExistsError(f"refusing to replace existing config: {target}")
    # Bootstrap has no client yet. Do not create placeholder credentials or mutate the DB.
    config = build_reality_bootstrap_config(options["name"], options["port"], options["sni"],
                                            options["private_key"], options["short_id"])
    validate_config(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=target.parent, delete=False) as tmp:
        json.dump(config, tmp, indent=2)
        tmp.write("\n")
        temp_path = Path(tmp.name)
    try:
        subprocess.run([str(options["binary"]), "run", "-test", "-config", str(temp_path)], check=True)
    except Exception as exc:
        print(f"Xray config verification failed: {exc}", file=sys.stderr)
        return 1
    finally:
        temp_path.unlink(missing_ok=True)
    if not options.get("apply"):
        print(f"Validated; dry run only. Config not written: {target}")
        return 0
    if target.exists():
        raise FileExistsError(f"refusing to replace existing config: {target}")
    backup = target.with_suffix(target.suffix + ".bak")
    if backup.exists():
        raise FileExistsError(f"backup path already exists: {backup}")
    payload = json.dumps(config, indent=2) + "\n"
    backup.write_text(payload, encoding="utf-8")
    temp = target.with_suffix(target.suffix + ".new")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise FileExistsError(f"refusing to replace existing config: {target}")
        os.link(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Applied first-run config: {target}; backup: {backup}. No service or firewall changes.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--sni", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--short-id", required=True)
    parser.add_argument("--public-key", default="bootstrap-public-key")
    parser.add_argument("--database", type=Path, default=Path(os.getenv("DATABASE_PATH", "data/vless-control.sqlite3")))
    parser.add_argument("--config", type=Path, default=Path(os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")))
    parser.add_argument("--binary", type=Path, default=Path(os.getenv("XRAY_BINARY", "/usr/local/bin/xray")))
    parser.add_argument("--apply", action="store_true", help="verify, back up candidate, and atomically create config (first-run only)")
    args = vars(parser.parse_args())
    try:
        raise SystemExit(run(args))
    except Exception as exc:
        raise SystemExit(f"Bootstrap stopped ({type(exc).__name__}): {exc}") from exc


if __name__ == "__main__":
    main()
