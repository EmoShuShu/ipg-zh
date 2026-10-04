from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .builder import build_outputs
from .core import ROOT, dump_yaml, load_json, load_yaml, sha256_bytes, validate_schema, walk_nodes
from .full_parser import extract_full_pdf, parse_full_extraction
from .pilot_parser import OFFICIAL_SHA256
from .reconcile import reconcile_full_document
from .validation import validate_release


OFFICIAL_PDF = (
    ROOT
    / "snapshots/official/2024-09-23"
    / OFFICIAL_SHA256
    / "MTG_IPG_2024Sep23_EN.pdf"
)
P2_RELEASE = ROOT / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001"
P3_ROOT = ROOT / "outputs/p3"
EXTRACTION = P3_ROOT / "work/full-extraction.json"
PARSED = P3_ROOT / "work/full-parsed.json"
SOURCE = P3_ROOT / "source/full-official.yaml"
MANIFEST = P3_ROOT / "source/manifest.yaml"
REPORTS = P3_ROOT / "reports"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, content: Any) -> None:
    _write_text(path, json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _previous_documents() -> list[dict[str, Any]]:
    return [
        load_yaml(P2_RELEASE / filename)
        for filename in ("chapter-02.yaml", "appendix-a.yaml", "appendix-b.yaml")
    ]


def p3_manifest() -> dict[str, Any]:
    prior = load_yaml(P2_RELEASE / "manifest.yaml")
    manifest = copy.deepcopy(prior)
    manifest["releaseId"] = "ipg-2024-09-23__ann-aipg-legacy-pilot__zh-r0001-p3"
    manifest["scope"] = {
        "officialContent": {"mode": "full-document", "included": ["full-official-ipg-2024-09-23"]},
        "publicationAnnotations": {
            "mode": "pilot",
            "includedSections": ["2.5"],
            "deferredGroups": 86,
            "deferredRawUnits": 338,
        },
    }
    manifest["publishable"] = False
    manifest["documents"] = ["full-official.yaml"]
    return manifest


def command_parse(_: argparse.Namespace) -> int:
    extraction = extract_full_pdf(OFFICIAL_PDF)
    parsed = parse_full_extraction(extraction)
    _write_json(EXTRACTION, extraction)
    _write_json(PARSED, parsed)
    _write_json(REPORTS / "pdf-coverage.json", parsed["coverage"])
    print(
        f"full official parse: {len(parsed['document']['sections'])} sections; "
        f"{parsed['coverage']['classifiedLineCount']}/{parsed['coverage']['lineCount']} lines classified"
    )
    return 0


def command_reconcile(_: argparse.Namespace) -> int:
    if not PARSED.exists():
        raise SystemExit("run 'ipg-p3 parse' first")
    result = reconcile_full_document(
        load_json(PARSED),
        _previous_documents(),
        load_yaml(ROOT / "src/ipg/id-registry.yaml"),
        version="ipg-2024-09-23-p3",
    )
    findings = result["reconciliation"]["findings"]
    _write_json(REPORTS / "reconciliation.json", result["reconciliation"])
    if findings:
        raise SystemExit(f"reconciliation failed closed with {len(findings)} findings")
    schema_errors = validate_schema(result["document"], ROOT / "schema/ipg-source.schema.json")
    if schema_errors:
        raise SystemExit("source schema validation failed: " + "; ".join(schema_errors))
    _write_text(SOURCE, dump_yaml(result["document"]))
    _write_text(MANIFEST, dump_yaml(p3_manifest()))
    _write_text(ROOT / "src/ipg/id-registry.yaml", dump_yaml(result["registry"]))
    print(
        f"reconciled full document; preserved {result['reconciliation']['preservedPriorIdCount']} P2 ids; "
        f"registry entries={len(result['registry']['entries'])}"
    )
    return 0


def _migration_stub() -> dict[str, Any]:
    return {
        "scope": "P3 official-English-only; legacy migration deliberately not run",
        "coverage": {"rawUnitCount": 0, "disposedUnitCount": 0, "duplicateConsumption": 0, "dispositions": {}},
        "findings": [],
    }


def command_build(_: argparse.Namespace) -> int:
    if not SOURCE.exists():
        raise SystemExit("run 'ipg-p3 reconcile' first")
    manifest = load_yaml(MANIFEST)
    document = load_yaml(SOURCE)
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    manifest_errors = validate_schema(manifest, ROOT / "schema/ipg-manifest.schema.json")
    if manifest_errors:
        raise SystemExit("manifest schema validation failed: " + "; ".join(manifest_errors))
    candidate = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=[document],
        display_values=display,
        migration_report=_migration_stub(),
        review_ledger=None,
    )
    release = validate_release(
        profile="release",
        manifest=manifest,
        documents=[document],
        display_values=display,
        migration_report=_migration_stub(),
        review_ledger=None,
    )
    _write_json(REPORTS / "validation-candidate.json", candidate)
    _write_json(REPORTS / "validation-release.json", release)
    if not candidate["valid"]:
        raise SystemExit("P3 candidate validation failed")
    if release["valid"]:
        raise SystemExit("P3 nonpublishable candidate unexpectedly passed release validation")
    first = P3_ROOT / "determinism/first"
    second = P3_ROOT / "determinism/second"
    report_one = build_outputs(first, manifest, [document], display, candidate=True, profile="candidate")
    report_two = build_outputs(second, manifest, [document], display, candidate=True, profile="candidate")
    filenames = ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json")
    deterministic = all((first / name).read_bytes() == (second / name).read_bytes() for name in filenames)
    if not deterministic:
        raise SystemExit("full candidate build is not byte deterministic")
    output_errors = validate_schema(load_json(first / "rules.json"), ROOT / "schema/ipg-output.schema.json")
    if output_errors:
        raise SystemExit("output schema validation failed: " + "; ".join(output_errors))
    candidate_dir = P3_ROOT / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (candidate_dir / name).write_bytes((first / name).read_bytes())
    _write_json(
        REPORTS / "determinism.json",
        {
            "byteIdentical": deterministic,
            "files": {
                name: sha256_bytes((first / name).read_bytes()) for name in filenames
            },
            "firstBuild": report_one,
            "secondBuild": report_two,
        },
    )
    print(f"P3 candidate built under outputs/p3/candidate; deterministic={deterministic}")
    return 0


def _chapter_for(number: str) -> str:
    return number.split(".", 1)[0] if number[:1].isdigit() else number


def command_review(_: argparse.Namespace) -> int:
    if not SOURCE.exists():
        raise SystemExit("run 'ipg-p3 reconcile' first")
    document = load_yaml(SOURCE)
    parsed = load_json(PARSED)
    reconciliation = load_json(REPORTS / "reconciliation.json")
    sections = document["sections"]
    infractions = [section for section in sections if section["kind"] == "infraction"]
    role_counts = Counter(
        component["role"] for section in sections for component in section["components"]
    )
    group_counts = Counter(
        group["kind"]
        for section in sections
        for component in section["components"]
        for group in component["groups"]
    )
    block_counts = Counter(
        block["type"] for kind, block in walk_nodes([document]) if kind == "block"
    )
    section_counts = Counter(section["kind"] for section in sections)
    appendix_a = next(section for section in sections if section["number"] == "A")
    appendix_b = next(section for section in sections if section["number"] == "B")
    p2_ids = {
        node["id"]
        for prior in _previous_documents()
        for _, node in walk_nodes([prior])
    }
    full_ids = {node["id"] for _, node in walk_nodes([document])}
    summary = {
        "schemaVersion": 1,
        "authority": {"pdfSha256": parsed["pdf"]["sha256"], "pages": parsed["pdf"]["pages"]},
        "structureCounts": {
            "sections": dict(sorted(section_counts.items())),
            "components": sum(role_counts.values()),
            "groups": sum(group_counts.values()),
            "blocks": sum(block_counts.values()),
            "roles": dict(sorted(role_counts.items())),
            "groupKinds": dict(sorted(group_counts.items())),
            "blockTypes": dict(sorted(block_counts.items())),
        },
        "structureTree": [
            {
                "number": section["number"],
                "id": section["id"],
                "kind": section["kind"],
                "title": section["title"]["en"],
                "roles": [component["role"] for component in section["components"]],
            }
            for section in sections
        ],
        "infractions": [
            {
                "number": section["number"],
                "id": section["id"],
                "title": section["title"]["en"],
                "penaltyCode": section["penaltyCode"],
            }
            for section in infractions
        ],
        "appendixA": {
            "rowCount": sum(len(group["blocks"]) for component in appendix_a["components"] for group in component["groups"]),
            "unknownReferences": 0,
            "penaltyMismatches": 0,
        },
        "appendixB": {
            "dateGroups": [
                {"date": group["date"], "entryCount": len(group["blocks"])}
                for group in appendix_b["components"][0]["groups"]
            ],
            "entryCount": sum(len(group["blocks"]) for group in appendix_b["components"][0]["groups"]),
            "referencedRules": sorted(
                {
                    reference
                    for group in appendix_b["components"][0]["groups"]
                    for block in group["blocks"]
                    for reference in block.get("ruleReferences", [])
                }
            ),
        },
        "coverage": parsed["coverage"],
        "reconciliation": {
            "findingCount": len(reconciliation["findings"]),
            "findings": reconciliation["findings"],
            "p2IdCount": len(p2_ids),
            "p2IdsPreserved": len(p2_ids & full_ids),
            "missingP2Ids": sorted(p2_ids - full_ids),
        },
    }
    _write_json(REPORTS / "p3-review-summary.json", summary)
    print(
        f"P3 review summary: {len(sections)} sections, {len(infractions)} infractions, "
        f"P2 ids preserved={len(p2_ids & full_ids)}/{len(p2_ids)}"
    )
    return 0


def command_all(args: argparse.Namespace) -> int:
    for command in (command_parse, command_reconcile, command_build, command_review):
        command(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipg-p3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("parse").set_defaults(func=command_parse)
    subparsers.add_parser("reconcile").set_defaults(func=command_reconcile)
    subparsers.add_parser("build").set_defaults(func=command_build)
    subparsers.add_parser("review").set_defaults(func=command_review)
    subparsers.add_parser("all").set_defaults(func=command_all)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
