from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .core import ROOT, dump_yaml, load_json, load_yaml
from .migration import migrate_pilot
from .pilot_parser import OFFICIAL_SHA256, parse_pilot


OFFICIAL_PDF = (
    ROOT
    / "snapshots/official/2024-09-23"
    / OFFICIAL_SHA256
    / "MTG_IPG_2024Sep23_EN.pdf"
)
RELEASE_DIR = ROOT / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001"
PARSED_JSON = ROOT / "work/parsed/official-pilot.json"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def command_parse(_: argparse.Namespace) -> int:
    parsed = parse_pilot(OFFICIAL_PDF)
    _write_json(PARSED_JSON, parsed)
    print(f"parsed approved pilot pages -> {PARSED_JSON.relative_to(ROOT)}")
    return 0


def command_migrate(_: argparse.Namespace) -> int:
    if not PARSED_JSON.exists():
        raise SystemExit("run 'ipg-pilot parse' first")
    result = migrate_pilot(
        load_json(PARSED_JSON),
        ROOT / "AIPG_2025.md",
        load_yaml(ROOT / "src/ipg/id-registry.yaml"),
        load_yaml(ROOT / "src/ipg/mapping-overrides.yaml"),
    )
    for filename, document in result["documents"].items():
        _write_text(RELEASE_DIR / filename, dump_yaml(document))
    _write_text(ROOT / "src/ipg/id-registry.yaml", dump_yaml(result["registry"]))
    _write_json(ROOT / "work/migration/raw-units.json", result["rawUnits"])
    _write_json(ROOT / "work/migration/raw-unit-ledger.json", result["coverageLedger"])
    _write_json(
        ROOT / "reports/migration-report.json",
        {
            "scope": "pilot-only",
            "coverage": result["coverage"],
            "findingCounts": result["findingCounts"],
            "findings": result["findings"],
        },
    )
    print(
        "migrated pilot: "
        f"{result['coverage']['disposedUnitCount']}/{result['coverage']['rawUnitCount']} raw units disposed; "
        f"{sum(result['findingCounts'].values())} findings"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipg-pilot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("parse", help="parse only the approved PDF pilot pages").set_defaults(func=command_parse)
    subparsers.add_parser("migrate", help="migrate legacy pilot content and write coverage ledger").set_defaults(func=command_migrate)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

