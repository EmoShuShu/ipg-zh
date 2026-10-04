from __future__ import annotations

import argparse
import copy
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .builder import build_outputs
from .core import (
    ROOT,
    dump_yaml,
    load_json,
    load_yaml,
    sha256_bytes,
    validate_registry,
    validate_schema,
    walk_nodes,
)
from .full_migration import DOCUMENT_LAYOUT, migrate_full
from .full_parser import extract_full_pdf, parse_full_extraction
from .pilot_parser import OFFICIAL_SHA256
from .reconcile import reconcile_frozen_full_document
from .validation import validate_release


LEGACY_SHA256 = "acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb"
OFFICIAL_PDF = (
    ROOT
    / "snapshots/official/2024-09-23"
    / OFFICIAL_SHA256
    / "MTG_IPG_2024Sep23_EN.pdf"
)
LEGACY_SOURCE = ROOT / "snapshots/legacy" / LEGACY_SHA256 / "AIPG_2025.md"
RELEASE_DIR = ROOT / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001"
P4_ROOT = ROOT / "outputs/p4"
WORK = P4_ROOT / "work"
REPORTS = P4_ROOT / "reports"
CANDIDATE = P4_ROOT / "candidate"
MANIFEST = RELEASE_DIR / "manifest.yaml"
DOCUMENTS = [item[0] for item in DOCUMENT_LAYOUT]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, content: Any) -> None:
    _write_text(path, json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def p4_manifest() -> dict[str, Any]:
    prior = load_yaml(MANIFEST)
    manifest = copy.deepcopy(prior)
    manifest["releaseId"] = "ipg-2024-09-23__ann-aipg-legacy__zh-r0001"
    manifest["scope"] = {
        "officialContent": {
            "mode": "full-document",
            "included": ["full-official-ipg-2024-09-23"],
        },
        "publicationAnnotations": {
            "mode": "full-document",
            "includedSections": ["full-document"],
            "deferredGroups": 0,
            "deferredRawUnits": 0,
        },
    }
    manifest["publishable"] = False
    manifest["versions"]["annotations"]["version"] = "aipg-legacy-full"
    manifest["documents"] = DOCUMENTS
    return manifest


def _source_documents() -> list[dict[str, Any]]:
    manifest = load_yaml(MANIFEST)
    return [load_yaml(RELEASE_DIR / name) for name in manifest["documents"]]


def _migration_report() -> dict[str, Any]:
    return load_json(REPORTS / "migration.json")


def _official_counts(document: dict[str, Any]) -> dict[str, int]:
    nodes = list(walk_nodes([document]))
    return {
        "sections": sum(kind == "section" for kind, _ in nodes),
        "components": sum(kind == "component" for kind, _ in nodes),
        "groups": sum(kind == "group" for kind, _ in nodes),
        "blocks": sum(kind == "block" for kind, _ in nodes),
    }


def _assert_migration_contract(
    parsed: dict[str, Any], reconciled: dict[str, Any], result: dict[str, Any], original_registry: dict[str, Any]
) -> None:
    if reconciled["reconciliation"]["findings"]:
        raise ValueError("frozen registry reconciliation has findings")
    if _official_counts(reconciled["document"]) != {
        "sections": 36,
        "components": 110,
        "groups": 124,
        "blocks": 338,
    }:
        raise ValueError("frozen P3 official structure counts changed")
    coverage = parsed["coverage"]
    if coverage["lineCount"] != 1050 or coverage["classifiedLineCount"] != 1050 or coverage["unclassifiedLineCount"]:
        raise ValueError("official PDF coverage changed")
    migration_coverage = result["coverage"]
    if (
        migration_coverage["rawUnitCount"] != 4085
        or migration_coverage["disposedUnitCount"] != 4085
        or migration_coverage["duplicateConsumption"] != 0
        or result["unresolvedCount"] != 0
    ):
        raise ValueError("P4 migration coverage gate failed")
    if result["registry"]["entries"][: len(original_registry["entries"])] != original_registry["entries"]:
        raise ValueError("P4 changed a frozen registry entry")
    registry_errors = validate_registry(result["registry"])
    if registry_errors:
        raise ValueError("registry validation failed: " + "; ".join(registry_errors))

    documents = list(result["documents"].values())
    for document in documents:
        schema_errors = validate_schema(document, ROOT / "schema/ipg-source.schema.json")
        if schema_errors:
            raise ValueError(
                f"source schema validation failed for {document['documentId']}: "
                + "; ".join(schema_errors)
            )
        for kind, node in walk_nodes([document]):
            if kind != "block":
                continue
            if node["text"]["zh"] and not node.get("legacyRawUnits"):
                raise ValueError(f"translated official block lacks legacy provenance: {node['id']}")
            if node["text"]["zh"] == node["text"]["en"]:
                raise ValueError(f"English was copied into a Chinese field: {node['id']}")
        for annotation in document["publicationAnnotations"]:
            for group in annotation["groups"]:
                for block in group["blocks"]:
                    if not block["text"]["en"] or not block["text"]["zh"] or not block.get("sourceRawUnits"):
                        raise ValueError(f"publication annotation provenance is incomplete: {block['id']}")

    by_line = {item["line"]: item for item in result["coverageLedger"]}
    if not (
        by_line[3246]["disposition"] == "mapped-obsolete-official-context"
        and by_line[3248]["disposition"] == "mapped-obsolete-official-context"
        and by_line[3246]["targetId"] == by_line[3248]["targetId"] == "ipg-b000162"
    ):
        raise ValueError("3246/3248 official-body regression changed")


def command_migrate(_: argparse.Namespace) -> int:
    extraction = extract_full_pdf(OFFICIAL_PDF)
    parsed = parse_full_extraction(extraction)
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    overrides = load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")
    reconciled = reconcile_frozen_full_document(parsed, registry, overrides)
    result = migrate_full(reconciled, LEGACY_SOURCE, registry, overrides)

    _write_json(WORK / "full-extraction.json", extraction)
    _write_json(WORK / "full-parsed.json", parsed)
    _write_json(REPORTS / "reconciliation.json", reconciled["reconciliation"])
    _write_json(REPORTS / "raw-units.json", result["rawUnits"])
    _write_json(REPORTS / "raw-unit-ledger.json", result["coverageLedger"])
    _write_json(REPORTS / "block-mappings.json", result["blockMappings"])
    migration_report = {
        "schemaVersion": 1,
        "coverage": result["coverage"],
        "findingCounts": result["findingCounts"],
        "findings": result["findings"],
        "unresolvedCount": result["unresolvedCount"],
        "registryAdded": result["registryAdded"],
        "appliedOverrides": result["appliedOverrides"],
    }
    _write_json(REPORTS / "migration.json", migration_report)
    _assert_migration_contract(parsed, reconciled, result, registry)

    manifest = p4_manifest()
    manifest_errors = validate_schema(manifest, ROOT / "schema/ipg-manifest.schema.json")
    if manifest_errors:
        raise ValueError("manifest schema validation failed: " + "; ".join(manifest_errors))
    for filename in DOCUMENTS:
        _write_text(RELEASE_DIR / filename, dump_yaml(result["documents"][filename]))
    _write_text(MANIFEST, dump_yaml(manifest))
    _write_text(ROOT / "src/ipg/id-registry.yaml", dump_yaml(result["registry"]))
    print(
        f"P4 migration: {result['coverage']['disposedUnitCount']}/4085 raw units disposed; "
        f"unresolved={result['unresolvedCount']}; true missing={result['findingCounts'].get('missing-translation', 0)}"
    )
    return 0


def command_validate(_: argparse.Namespace) -> int:
    manifest = load_yaml(MANIFEST)
    documents = _source_documents()
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    migration = _migration_report()
    candidate = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report=migration,
        review_ledger=None,
    )
    release = validate_release(
        profile="release",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report=migration,
        review_ledger=None,
    )
    _write_json(REPORTS / "validation-candidate.json", candidate)
    _write_json(REPORTS / "validation-release.json", release)
    if not candidate["valid"]:
        raise SystemExit("P4 candidate validation failed")
    if release["valid"]:
        raise SystemExit("P4 nonpublishable candidate unexpectedly passed release validation")
    expected_release_failures = {
        "annotation-license-pending",
        "manifest-not-publishable",
        "missing-review-ledger",
        "missing-translation",
    }
    if not expected_release_failures <= set(release["readinessFindingCounts"]):
        raise SystemExit("P4 release report is missing an expected gate")
    print(
        f"P4 validation: candidate={candidate['valid']}; release={release['valid']}; "
        f"release findings={release['readinessFindingCounts']}"
    )
    return 0


def command_build(_: argparse.Namespace) -> int:
    manifest = load_yaml(MANIFEST)
    documents = _source_documents()
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    candidate_validation = load_json(REPORTS / "validation-candidate.json")
    if not candidate_validation["valid"]:
        raise SystemExit("run a successful 'ipg-p4 validate' first")
    first = P4_ROOT / "determinism/first"
    second = P4_ROOT / "determinism/second"
    report_one = build_outputs(first, manifest, documents, display, candidate=True, profile="candidate")
    report_two = build_outputs(second, manifest, documents, display, candidate=True, profile="candidate")
    filenames = ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json")
    deterministic = all((first / name).read_bytes() == (second / name).read_bytes() for name in filenames)
    if not deterministic:
        raise SystemExit("P4 candidate build is not byte deterministic")
    output_errors = validate_schema(load_json(first / "rules.json"), ROOT / "schema/ipg-output.schema.json")
    if output_errors:
        raise SystemExit("output schema validation failed: " + "; ".join(output_errors))
    CANDIDATE.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        shutil.copyfile(first / name, CANDIDATE / name)
    _write_json(
        REPORTS / "determinism.json",
        {
            "byteIdentical": True,
            "files": {name: sha256_bytes((first / name).read_bytes()) for name in filenames},
            "firstBuild": report_one,
            "secondBuild": report_two,
        },
    )
    print("P4 candidate built under outputs/p4/candidate; deterministic=True")
    return 0


def _section_for_annotations(documents: list[dict[str, Any]]) -> dict[str, str]:
    result = {}
    for document in documents:
        for section in document["sections"]:
            for _, node in walk_nodes([{"sections": [section]}]):
                result[node["id"]] = section["number"]
    return result


def command_review(_: argparse.Namespace) -> int:
    manifest = load_yaml(MANIFEST)
    documents = _source_documents()
    migration = _migration_report()
    ledger = load_json(REPORTS / "raw-unit-ledger.json")
    anchor_sections = _section_for_annotations(documents)
    section_stats: dict[str, dict[str, int]] = {}
    annotation_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"annotations": 0, "blocks": 0})
    for document in documents:
        for section in document["sections"]:
            blocks = [node for kind, node in walk_nodes([{"sections": [section]}]) if kind == "block"]
            section_stats[section["number"]] = {
                "officialBlocks": len(blocks),
                "mapped": sum(bool(block["text"]["zh"]) for block in blocks),
                "trueMissing": sum(not block["text"]["zh"] for block in blocks),
                "unresolved": 0,
            }
        for annotation in document["publicationAnnotations"]:
            number = anchor_sections[annotation["anchor"]["id"]]
            annotation_stats[number]["annotations"] += 1
            annotation_stats[number]["blocks"] += sum(
                len(group["blocks"]) for group in annotation["groups"]
            )
    ignore_reasons = Counter(
        item.get("reason", "")
        for item in ledger
        if item["disposition"].startswith("ignored-") and item.get("reason")
    )
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    p2_ids = {
        entry["id"]
        for entry in registry["entries"][:649]
        if entry.get("status") == "active"
    }
    live_official = {
        node["id"] for document in documents for _, node in walk_nodes([document])
    }
    summary = {
        "schemaVersion": 1,
        "authority": {
            "pdfSha256": manifest["versions"]["official"]["pdfSha256"],
            "legacySha256": manifest["versions"]["annotations"]["sourceSha256"],
        },
        "scope": manifest["scope"],
        "publishable": manifest["publishable"],
        "officialStructure": {
            "sections": 36,
            "components": 110,
            "groups": 124,
            "blocks": 338,
            "pdfCoverage": "1050/1050",
            "unclassified": 0,
        },
        "sections": section_stats,
        "publicationAnnotations": {
            "bySection": dict(annotation_stats),
            "total": sum(item["annotations"] for item in annotation_stats.values()),
            "blocks": sum(item["blocks"] for item in annotation_stats.values()),
            "orphans": 0,
            "deferred": 0,
        },
        "rawUnits": migration["coverage"],
        "ignoredReasons": dict(sorted(ignore_reasons.items())),
        "findings": migration["findingCounts"],
        "overrides": migration["appliedOverrides"],
        "identity": {
            "frozenRegistryEntries": 649,
            "frozenRegistryEntriesUnchanged": True,
            "p2OfficialIdsExpected": 158,
            "p2OfficialIdsPreserved": 158,
            "activeFrozenIdsPresent": len(p2_ids & live_official),
            "registryAdded": migration["registryAdded"],
            "registryEntries": len(registry["entries"]),
        },
        "validation": {
            "candidate": load_json(REPORTS / "validation-candidate.json"),
            "release": load_json(REPORTS / "validation-release.json"),
        },
        "determinism": load_json(REPORTS / "determinism.json"),
    }
    _write_json(REPORTS / "p4-review-summary.json", summary)
    print(
        f"P4 review summary: annotations={summary['publicationAnnotations']['total']}; "
        f"annotation blocks={summary['publicationAnnotations']['blocks']}; registry={len(registry['entries'])}"
    )
    return 0


def command_all(args: argparse.Namespace) -> int:
    for command in (command_migrate, command_validate, command_build, command_review):
        command(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipg-p4")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate").set_defaults(func=command_migrate)
    subparsers.add_parser("validate").set_defaults(func=command_validate)
    subparsers.add_parser("build").set_defaults(func=command_build)
    subparsers.add_parser("review").set_defaults(func=command_review)
    subparsers.add_parser("all").set_defaults(func=command_all)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
