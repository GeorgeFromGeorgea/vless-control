"""Read-only host preflight for supported Xray-control deployments."""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

SUPPORTED = {("Ubuntu", "22.04"), ("Ubuntu", "24.04"), ("Debian", "12")}
DEFAULT_PORTS = (80, 443, 8443, 2053, 10001)


@dataclass
class PreflightReport:
    supported_os: bool
    distribution: str
    release: str
    architecture: str
    python: str
    root: bool
    xray_present: bool
    xray_path: str | None
    config_present: bool
    config_path: str
    free_disk_gb: float | None
    memory_mb: int | None
    occupied_ports: dict[str, list[str]]
    services: dict[str, str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _read_os_release() -> tuple[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
    except OSError:
        return platform.system(), platform.release()
    distro = values.get("ID", "unknown").lower()
    pretty = values.get("PRETTY_NAME", distro).lower()
    if distro == "ubuntu":
        name = "Ubuntu"
    elif distro == "debian":
        name = "Debian"
    else:
        name = values.get("NAME", distro)
    release = values.get("VERSION_ID", "")
    if not release:
        release = "22.04" if "22.04" in pretty else "24.04" if "24.04" in pretty else "12" if "debian 12" in pretty else "unknown"
    return name, release


def _run(command: list[str], runner: Callable = subprocess.run) -> str:
    try:
        result = runner(command, check=False, capture_output=True, text=True, timeout=5)
        return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _ports(ports: tuple[int, ...], runner: Callable) -> dict[str, list[str]]:
    output = _run(["ss", "-H", "-ltnup"], runner)
    result = {str(port): [] for port in ports}
    for line in output.splitlines():
        if not line.strip():
            continue
        for port in ports:
            if f":{port} " in line or f":{port}\n" in line or line.rstrip().endswith(f":{port}"):
                result[str(port)].append(line.strip()[:300])
    return {port: lines for port, lines in result.items() if lines}


def _services(names: tuple[str, ...], runner: Callable) -> dict[str, str]:
    result = {}
    for name in names:
        result[name] = _run(["systemctl", "is-active", name], runner) or "unknown"
    return result


def collect(config_path: str | Path = "/usr/local/etc/xray/config.json",
            ports: tuple[int, ...] = DEFAULT_PORTS,
            runner: Callable = subprocess.run) -> PreflightReport:
    distro, release = _read_os_release()
    xray_path = shutil.which("xray")
    path = str(config_path)
    warnings: list[str] = []
    supported = (distro, release) in SUPPORTED
    if not supported:
        warnings.append("ОС не входит в поддерживаемый список: Ubuntu 22.04/24.04 или Debian 12")
    disk = shutil.disk_usage("/")
    free_bytes = getattr(disk, "free", disk[2])
    mem_mb = None
    try:
        mem_mb = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:"))) // 1024
    except (OSError, StopIteration, ValueError):
        warnings.append("Не удалось определить объём RAM")
    config_present = Path(path).is_file()
    if not config_present:
        warnings.append(f"Конфигурация Xray не найдена: {path}")
    if not xray_path:
        warnings.append("Бинарник xray не найден в PATH")
    return PreflightReport(
        supported_os=supported, distribution=distro, release=release,
        architecture=platform.machine(), python=platform.python_version(),
        root=(os.geteuid() == 0), xray_present=bool(xray_path), xray_path=xray_path,
        config_present=config_present, config_path=path,
        free_disk_gb=round(free_bytes / (1024 ** 3), 2), memory_mb=mem_mb,
        occupied_ports=_ports(ports, runner),
        services=_services(("xray", "nginx"), runner), warnings=warnings,
    )


def render(report: PreflightReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
