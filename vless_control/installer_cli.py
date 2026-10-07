"""Opt-in installer CLI. It never downloads blindly and never enables/starts Xray."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .installer import InstallPlan, install_artifact, verify_sha256
from .preflight import _read_os_release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="locally downloaded official Xray tar.gz")
    parser.add_argument("--sha256", required=True, help="expected SHA-256 from the official release checksums")
    parser.add_argument("--apply", action="store_true", help="install binary and write unit; still does not start/enable Xray")
    parser.add_argument("--replace-existing", action="store_true", help="allow replacement only with explicit backup")
    parser.add_argument("--backup-dir", type=Path, default=Path("/root/vless-control-backups"))
    parser.add_argument("--binary", type=Path, default=Path("/usr/local/bin/xray"))
    parser.add_argument("--config", type=Path, default=Path("/usr/local/etc/xray/config.json"))
    parser.add_argument("--unit", type=Path, default=Path("/etc/systemd/system/vless-control-xray.service"))
    args = parser.parse_args()

    distro, release = _read_os_release()
    existing_xray = args.binary.exists()
    existing_config = args.config.exists()
    plan = InstallPlan(
        distro, release, os.uname().machine, args.artifact, args.binary, args.config,
        args.unit, existing_xray, existing_config, args.replace_existing,
        args.replace_existing and (existing_xray or existing_config),
    )
    try:
        verify_sha256(args.artifact, args.sha256)
        plan.validate()
        print(f"Проверка пройдена: {distro} {release}, SHA-256 совпадает.")
        print(f"Бинарник: {args.binary}")
        print(f"Unit: {args.unit}")
        print(f"Конфигурация НЕ создаётся автоматически: {args.config}")
        if not args.apply:
            print("DRY RUN. Для записи бинарника и unit добавьте --apply.")
            return
        if args.replace_existing:
            from .installer import backup_paths
            backup = backup_paths([args.binary, args.config, args.unit], args.backup_dir)
            print(f"Backup: {backup}")
        install_artifact(plan)
        print("Установка файлов завершена. Xray НЕ включён и НЕ запущен.")
        print(f"Создайте и проверьте конфиг, затем вручную: {args.binary} run -test -config {args.config}")
    except Exception as exc:
        raise SystemExit(f"Установка остановлена ({type(exc).__name__}): {exc}") from exc


if __name__ == "__main__":
    main()
