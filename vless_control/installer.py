"""Planning/execution primitives for a conservative Xray installer.

The default CLI is a plan only. Execution is explicit and requires root,
supported OS, a release artifact, and no existing Xray service/config unless
--replace-existing is deliberately supplied by an operator.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .preflight import SUPPORTED


@dataclass(frozen=True)
class InstallPlan:
    distribution: str
    release: str
    architecture: str
    artifact: Path
    binary_path: Path
    config_path: Path
    unit_path: Path
    existing_xray: bool
    existing_config: bool
    replace_existing: bool
    backup_required: bool

    def validate(self) -> None:
        if not (self.distribution, self.release) in SUPPORTED:
            raise RuntimeError("Неподдерживаемая ОС. Разрешены Ubuntu 22.04/24.04 и Debian 12")
        if not self.artifact.is_file():
            raise FileNotFoundError(f"Артефакт Xray не найден: {self.artifact}")
        if self.existing_xray and not self.replace_existing:
            raise RuntimeError("Xray уже найден; отказ замены без --replace-existing")
        if self.existing_config and not self.replace_existing:
            raise RuntimeError("Конфигурация Xray уже найдена; отказ без --replace-existing")
        if self.replace_existing and not self.backup_required:
            raise RuntimeError("Замена существующей установки требует backup")


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: str | Path, expected: str) -> None:
    actual = sha256(path)
    if actual.lower() != expected.strip().split()[0].lower():
        raise ValueError(f"SHA-256 mismatch: expected {expected}, got {actual}")


def safe_members(archive: tarfile.TarFile, destination: Path) -> list[tarfile.TarInfo]:
    """Reject traversal, links and special files before extraction."""
    selected = []
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if os.path.commonpath((str(root), str(target))) != str(root):
            raise ValueError(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"links are not allowed: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"special files are not allowed: {member.name}")
        selected.append(member)
    return selected


def find_binary(extracted: Path) -> Path:
    candidates = [p for p in extracted.rglob("xray") if p.is_file()]
    if len(candidates) != 1:
        raise RuntimeError(f"Ожидался один файл xray в архиве, найдено: {len(candidates)}")
    return candidates[0]


def backup_paths(paths: list[Path], backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    archive = backup_dir / "xray-install-backup.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        for path in paths:
            if path.exists():
                out.add(path, arcname=path.as_posix().lstrip("/"))
    if not archive.is_file() or archive.stat().st_size == 0:
        raise IOError("backup creation/verification failed")
    with tarfile.open(archive, "r:gz") as check:
        check.getmembers()
    return archive


def install_artifact(plan: InstallPlan, *, runner: Callable = subprocess.run) -> None:
    """Install verified binary and minimal unit; never starts/enables service."""
    plan.validate()
    if os.geteuid() != 0:
        raise PermissionError("installer requires root")
    with tempfile.TemporaryDirectory(prefix="vless-control-xray-") as tmp:
        extracted = Path(tmp) / "extract"
        extracted.mkdir()
        with tarfile.open(plan.artifact, "r:*") as archive:
            members = safe_members(archive, extracted)
            archive.extractall(extracted, members=members)
        binary = find_binary(extracted)
        plan.binary_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, plan.binary_path)
        plan.binary_path.chmod(0o755)
        probe = runner([str(plan.binary_path), "version"], check=False, capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            plan.binary_path.unlink(missing_ok=True)
            raise RuntimeError(f"installed xray binary failed version probe: {probe.stderr[:500]}")
        plan.unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit = (
            "[Unit]\nDescription=Xray (VLESS Control)\nAfter=network-online.target\n"
            "Wants=network-online.target\n\n[Service]\nType=simple\n"
            f"ExecStart={plan.binary_path} run -config {plan.config_path}\n"
            "Restart=on-failure\nNoNewPrivileges=true\nPrivateTmp=true\n\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )
        plan.unit_path.write_text(unit, encoding="utf-8")
        plan.unit_path.chmod(0o644)
