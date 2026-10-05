from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from .builder import build_outputs
from .core import ROOT, dump_yaml, load_json, load_yaml
from .migration import migrate_pilot
from .omegat import (
    apply_writeback,
    collect_units,
    export_project,
    preview_writeback,
)
from .pilot_parser import OFFICIAL_SHA256, parse_pilot
from .pilot_parser import extract_pilot
from .reconcile import reconcile_pilot
from .review import (
    build_review_ledger,
    extract_translation_notes,
    review_status_report,
    terminology_audit,
)
from .review_pack import build_p2_review_pack
from .validation import validate_release


OFFICIAL_PDF = (
    ROOT
    / "snapshots/official/2024-09-23"
    / OFFICIAL_SHA256
    / "MTG_IPG_2024Sep23_EN.pdf"
)
RELEASE_DIR = ROOT / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001"
OUTPUT_ROOT = ROOT / "outputs"
EXTRACTED_JSON = OUTPUT_ROOT / "work/parsed/extracted-pilot.json"
PARSED_JSON = OUTPUT_ROOT / "work/parsed/official-pilot.json"
REPORTS_DIR = OUTPUT_ROOT / "reports"
OMEGAT_DIR = OUTPUT_ROOT / "omegat/ipg-pilot"
WRITEBACK_DIR = OUTPUT_ROOT / "work/omegat-candidate"
TRANSLATION_REVISION = "zh-r0001"
TRANSLATION_NOTES = ROOT / f"review/translation-notes/{TRANSLATION_REVISION}.json"
REVIEW_LEDGER = ROOT / f"review/status/{TRANSLATION_REVISION}.json"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def command_parse(_: argparse.Namespace) -> int:
    extracted = extract_pilot(OFFICIAL_PDF)
    _write_json(EXTRACTED_JSON, extracted)
    print(f"parsed approved pilot pages with temporary ids -> {EXTRACTED_JSON.relative_to(ROOT)}")
    return 0


def command_reconcile(_: argparse.Namespace) -> int:
    if not EXTRACTED_JSON.exists():
        raise SystemExit("run 'ipg-pilot parse' first")
    previous = {
        filename: load_yaml(RELEASE_DIR / filename)
        for filename in ("chapter-02.yaml", "appendix-a.yaml", "appendix-b.yaml")
    }
    reconciled = reconcile_pilot(
        load_json(EXTRACTED_JSON),
        previous,
        load_yaml(ROOT / "src/ipg/mapping-overrides.yaml"),
        load_yaml(ROOT / "src/ipg/id-registry.yaml"),
    )
    _write_json(PARSED_JSON, reconciled)
    _write_json(REPORTS_DIR / "reconciliation-report.json", {
        "idStatus": reconciled["idStatus"],
        "findingCount": len(reconciled["reconciliationFindings"]),
        "findings": reconciled["reconciliationFindings"],
    })
    print(f"reconciled stable ids -> {PARSED_JSON.relative_to(ROOT)}")
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
    _write_json(OUTPUT_ROOT / "work/migration/raw-units.json", result["rawUnits"])
    _write_json(OUTPUT_ROOT / "work/migration/raw-unit-ledger.json", result["coverageLedger"])
    _write_json(
        REPORTS_DIR / "migration-report.json",
        {
            "scope": "pilot-only",
            "coverage": result["coverage"],
            "findingCounts": result["findingCounts"],
            "findings": result["findings"],
            "blockMappings": result["blockMappings"],
        },
    )
    print(
        "migrated pilot: "
        f"{result['coverage']['disposedUnitCount']}/{result['coverage']['rawUnitCount']} raw units disposed; "
        f"{sum(result['findingCounts'].values())} findings"
    )
    return 0


def _release_inputs() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [load_yaml(RELEASE_DIR / filename) for filename in manifest["documents"]]
    display_values = load_yaml(ROOT / "src/ipg/display-values.yaml")
    migration_report = load_json(REPORTS_DIR / "migration-report.json")
    return manifest, documents, display_values, migration_report


def _document_tuples() -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [(filename, load_yaml(RELEASE_DIR / filename)) for filename in manifest["documents"]]
    return documents, load_yaml(ROOT / "src/ipg/display-values.yaml")


def command_validate(args: argparse.Namespace) -> int:
    manifest, documents, display_values, migration_report = _release_inputs()
    review_path = REVIEW_LEDGER
    report = validate_release(
        profile=args.profile,
        manifest=manifest,
        documents=documents,
        display_values=display_values,
        migration_report=migration_report,
        review_ledger=load_json(review_path) if review_path.exists() else None,
    )
    path = REPORTS_DIR / f"validation-{args.profile}.json"
    _write_json(path, report)
    print(
        f"{args.profile}: {'valid' if report['valid'] else 'failed'}; "
        f"release gates={report['releaseGateCounts']}"
    )
    return 0 if report["valid"] else 1


def command_build(args: argparse.Namespace) -> int:
    manifest, documents, display_values, migration_report = _release_inputs()
    review_path = REVIEW_LEDGER
    report = validate_release(
        profile=args.profile,
        manifest=manifest,
        documents=documents,
        display_values=display_values,
        migration_report=migration_report,
        review_ledger=load_json(review_path) if review_path.exists() else None,
    )
    if not report["valid"]:
        raise SystemExit(f"{args.profile} validation failed; build refused")
    build = build_outputs(
        ROOT / ("pilot/output" if args.profile == "candidate" else "dist"),
        manifest,
        documents,
        display_values,
        candidate=args.profile == "candidate",
        profile=args.profile,
    )
    _write_json(REPORTS_DIR / f"validation-{args.profile}.json", report)
    destination = "pilot/output" if build["candidate"] else "dist"
    print(f"built {'candidate' if build['candidate'] else 'release'} outputs -> {destination}")
    return 0


def command_omegat_export(_: argparse.Namespace) -> int:
    documents, display_values = _document_tuples()
    mapping = export_project(
        OMEGAT_DIR,
        ROOT,
        documents,
        display_values,
        ROOT / "terminology/ipg-glossary.txt",
    )
    shutil.copyfile(
        ROOT / "tests/fixtures/pilot/translation-notes.tmx",
        OMEGAT_DIR / "tm/translation-notes.tmx",
    )
    print(f"exported {len(mapping['units'])} OmegaT units")
    return 0


def command_omegat_preview(_: argparse.Namespace) -> int:
    preview = preview_writeback(
        OMEGAT_DIR,
        ROOT,
        WRITEBACK_DIR,
    )
    _write_json(REPORTS_DIR / "omegat-writeback-preview.json", preview)
    print(f"previewed {preview['expectedChangeCount']} isolated candidate changes")
    return 0


def command_omegat_apply(args: argparse.Namespace) -> int:
    preview = load_json(REPORTS_DIR / "omegat-writeback-preview.json")
    applied = apply_writeback(
        OMEGAT_DIR,
        ROOT,
        WRITEBACK_DIR,
        preview,
        expected_change_count=args.expected_change_count,
    )
    _write_json(REPORTS_DIR / "omegat-writeback-apply.json", applied)
    print(f"applied {args.expected_change_count} previewed changes")
    return 0


def command_translation_notes(_: argparse.Namespace) -> int:
    documents, display_values = _document_tuples()
    units = collect_units(documents, display_values)
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    notes = extract_translation_notes(
        OMEGAT_DIR / "tm/translation-notes.tmx",
        units,
        release_id=manifest["releaseId"],
        translation_revision=manifest["versions"]["translation"]["revision"],
    )
    _write_json(TRANSLATION_NOTES, notes)
    print(f"imported {len(notes['notes'])} translation notes")
    return 0


def command_review(_: argparse.Namespace) -> int:
    documents, display_values = _document_tuples()
    units = collect_units(documents, display_values)
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    actions = load_yaml(ROOT / "tests/fixtures/pilot/review-actions.yaml")["actions"]
    ledger = build_review_ledger(
        units,
        actions,
        release_id=manifest["releaseId"],
        translation_revision=manifest["versions"]["translation"]["revision"],
    )
    _write_json(REVIEW_LEDGER, ledger)
    _write_json(REPORTS_DIR / "review-status.json", review_status_report(ledger))
    print(f"recorded {len(ledger['entries'])} review states")
    return 0


def command_terms(_: argparse.Namespace) -> int:
    documents, display_values = _document_tuples()
    report = terminology_audit(
        ROOT / "terminology/ipg-glossary.txt",
        collect_units(documents, display_values),
    )
    _write_json(REPORTS_DIR / "terminology-audit.json", report)
    print(
        f"terminology audit (non-blocking): {report['findingCount']} findings, "
        f"{report['glossaryIssueCount']} glossary issues"
    )
    return 0


def command_p2_pack(_: argparse.Namespace) -> int:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents, display_values = _document_tuples()
    summary = build_p2_review_pack(
        root=ROOT,
        manifest=manifest,
        documents=documents,
        display_values=display_values,
        migration_report=load_json(REPORTS_DIR / "migration-report.json"),
        report_dir=REPORTS_DIR,
    )
    print(
        "P2 review pack: "
        f"2.5 annotations={summary['section2_5PublicationAnnotations']['annotationCount']}; "
        f"deterministic={summary['deterministicBuild']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipg-pilot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("parse", help="parse only the approved PDF pilot pages").set_defaults(func=command_parse)
    subparsers.add_parser("reconcile", help="reconcile temporary extraction ids to stable ids").set_defaults(func=command_reconcile)
    subparsers.add_parser("migrate", help="migrate legacy pilot content and write coverage ledger").set_defaults(func=command_migrate)
    validate = subparsers.add_parser("validate", help="validate with an explicit profile")
    validate.add_argument("--profile", required=True, choices=("candidate", "release"))
    validate.set_defaults(func=command_validate)
    build = subparsers.add_parser("build", help="build after validation with an explicit profile")
    build.add_argument("--profile", required=True, choices=("candidate", "release"))
    build.set_defaults(func=command_build)
    subparsers.add_parser("omegat-export", help="export the pilot OmegaT project").set_defaults(
        func=command_omegat_export
    )
    subparsers.add_parser("omegat-preview", help="preview target PO writeback").set_defaults(
        func=command_omegat_preview
    )
    apply = subparsers.add_parser("omegat-apply", help="apply an unchanged writeback preview")
    apply.add_argument("--expected-change-count", type=int, required=True)
    apply.set_defaults(func=command_omegat_apply)
    subparsers.add_parser("translation-notes", help="extract TMX notes to the isolated store").set_defaults(
        func=command_translation_notes
    )
    subparsers.add_parser("review-status", help="build the independent review ledger").set_defaults(
        func=command_review
    )
    subparsers.add_parser("terms", help="run the non-blocking terminology audit").set_defaults(
        func=command_terms
    )
    subparsers.add_parser("p2-pack", help="regenerate the compact P2 review evidence").set_defaults(
        func=command_p2_pack
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

