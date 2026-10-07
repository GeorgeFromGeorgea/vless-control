"""Read-only preflight command; it never installs, writes, restarts, or changes firewall."""
from __future__ import annotations

import argparse
from .preflight import collect, render


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/usr/local/etc/xray/config.json")
    args = parser.parse_args()
    report = collect(args.config)
    print(render(report), end="")
    raise SystemExit(0 if report.supported_os else 2)


if __name__ == "__main__":
    main()
