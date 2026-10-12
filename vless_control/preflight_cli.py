"""Read-only preflight command; it never installs, writes, restarts, or changes firewall."""
from __future__ import annotations

import argparse
from .preflight import collect, render_human, render_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/usr/local/etc/xray/config.json")
    parser.add_argument("--json", action="store_true", help="вывести технический JSON")
    args = parser.parse_args()
    report = collect(args.config)
    print((render_json(report) if args.json else render_human(report)), end="")
    raise SystemExit(0 if report.supported_os else 2)


if __name__ == "__main__":
    main()
