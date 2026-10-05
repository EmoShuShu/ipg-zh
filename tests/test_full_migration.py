from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import pytest

from ipg_pipeline.builder import build_outputs
from ipg_pipeline.core import ROOT, load_yaml, validate_schema, walk_nodes
from ipg_pipeline.full_migration import (
    _annotation_override_by_start,
    _dispose,
    _map_annotations,
    migrate_full,
)
from ipg_pipeline.full_parser import extract_full_pdf, parse_full_extraction
from ipg_pipeline.migration import tokenize_legacy
from ipg_pipeline.p4 import DOCUMENTS, LEGACY_SOURCE, OFFICIAL_PDF, RELEASE_DIR, p4_manifest
from ipg_pipeline.reconcile import reconcile_frozen_full_document
from ipg_pipeline.validation import validate_release


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def _p4() -> tuple[dict, dict, dict]:
    parsed = parse_full_extraction(extract_full_pdf(OFFICIAL_PDF))
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    overrides = load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")
    reconciled = reconcile_frozen_full_document(parsed, registry, overrides)
    result = migrate_full(reconciled, LEGACY_SOURCE, registry, overrides)
    return parsed, reconciled, result


def _documents() -> list[dict]:
    return [load_yaml(RELEASE_DIR / filename) for filename in DOCUMENTS]


def _all_annotations(documents: list[dict]) -> list[dict]:
    return [annotation for document in documents for annotation in document["publicationAnnotations"]]


def test_all_4085_raw_units_have_one_specific_disposition() -> None:
    _, _, result = _p4()
    ledger = result["coverageLedger"]
    assert result["coverage"]["legacySha256"] == "acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb"
    assert len(ledger) == len({item["rawUnitId"] for item in ledger}) == 4085
    assert result["coverage"]["duplicateConsumption"] == 0
    assert result["unresolvedCount"] == 0
    assert not any(item["disposition"].startswith("deferred") for item in ledger)


def test_preamble_toc_and_demo_material_never_enter_published_content() -> None:
    _, _, result = _p4()
    preamble = [item for item in result["coverageLedger"] if item["line"] <= 70]
    assert len(preamble) == 70
    assert all(item["disposition"] == "ignored-with-reason" for item in preamble)
    published_raw = {
        raw_id
        for document in result["documents"].values()
        for _, node in walk_nodes([document])
        for raw_id in node.get("legacyRawUnits", [])
    }
    published_raw.update(
        raw_id
        for document in result["documents"].values()
        for annotation in document["publicationAnnotations"]
        for group in annotation["groups"]
        for block in group["blocks"]
        for raw_id in block["sourceRawUnits"]
    )
    assert not published_raw & {f"raw-L{line:06d}" for line in range(1, 71)}


def test_front_matter_and_chapters_1_3_4_have_conservative_legacy_mappings() -> None:
    documents = _documents()
    sections = {section["number"]: section for document in documents for section in document["sections"]}
    for number in ("introduction", "framework", "1.1", "3.5", "4.6"):
        section = sections[number]
        assert section["title"]["zh"]
        blocks = [node for kind, node in walk_nodes([{"sections": [section]}]) if kind == "block"]
        assert blocks and all(block["text"]["zh"] and block["legacyRawUnits"] for block in blocks)
    assert sections["4.6"]["title"]["en"] == "Theft of Tournament Material"
    assert sections["4.6"]["id"] == "ipg-s4-6"


def test_true_missing_translations_are_only_the_twelve_appendix_b_entries() -> None:
    _, _, result = _p4()
    missing = [finding for finding in result["findings"] if finding["code"] == "missing-translation"]
    assert len(missing) == 12
    appendix = result["documents"]["appendix-b.yaml"]["sections"][0]
    groups = appendix["components"][0]["groups"]
    assert [(group["date"], sum(bool(block["text"]["zh"]) for block in group["blocks"])) for group in groups] == [
        ("September 23, 2024", 5),
        ("April 15, 2024", 0),
        ("February 2, 2024", 0),
        ("November 13, 2023", 0),
        ("September 4, 2023", 0),
    ]
    assert all("officialText" in finding and "searchRange" in finding for finding in missing)
    assert sum(bool(finding["legacyEnglishEvidence"]) for finding in missing) == 7


def test_multiparagraph_and_list_annotations_keep_bilingual_raw_provenance() -> None:
    _, _, result = _p4()
    annotations = _all_annotations(list(result["documents"].values()))
    long_annotation = next(
        annotation
        for annotation in annotations
        if any(
            "raw-L001206" in block["sourceRawUnits"]
            for group in annotation["groups"]
            for block in group["blocks"]
        )
    )
    assert [group["kind"] for group in long_annotation["groups"]] == [
        "paragraphs", "unordered-list", "paragraphs", "unordered-list", "paragraphs"
    ]
    assert sum(len(group["blocks"]) for group in long_annotation["groups"]) == 14
    assert all(
        block["text"]["en"] and block["text"]["zh"] and len(block["sourceRawUnits"]) >= 2
        for group in long_annotation["groups"]
        for block in group["blocks"]
    )


def test_3246_and_3248_are_official_body_evidence_not_annotation() -> None:
    _, _, result = _p4()
    by_line = {item["line"]: item for item in result["coverageLedger"]}
    assert by_line[3246]["disposition"] == by_line[3248]["disposition"] == "mapped-obsolete-official-context"
    assert by_line[3246]["targetId"] == by_line[3248]["targetId"] == "ipg-b000162"
    annotation_raw = {
        raw_id
        for document in result["documents"].values()
        for annotation in document["publicationAnnotations"]
        for group in annotation["groups"]
        for block in group["blocks"]
        for raw_id in block["sourceRawUnits"]
    }
    assert {"raw-L003246", "raw-L003248"}.isdisjoint(annotation_raw)


def test_annotation_unknown_anchor_ambiguous_boundary_and_duplicate_consumption_fail_closed() -> None:
    _, reconciled, _ = _p4()
    units, _ = tokenize_legacy(LEGACY_SOURCE)
    overrides = copy.deepcopy(load_yaml(ROOT / "src/ipg/mapping-overrides.yaml"))
    boundary = next(item for item in overrides["applied"] if item.get("kind") == "publication-annotation-boundaries")
    boundary["anchor"]["id"] = "ipg-missing"
    with pytest.raises(ValueError, match="anchor does not exist"):
        _map_annotations(copy.deepcopy(reconciled["document"]), units, {}, overrides, [])

    duplicate = copy.deepcopy(boundary)
    duplicate["id"] = "duplicate-boundary"
    boundary["anchor"]["id"] = "ipg-s2-1-c-additional-remedy-g01-b005"
    with pytest.raises(ValueError, match="ambiguous publication annotation boundary"):
        _annotation_override_by_start({"applied": [boundary, duplicate]})

    disposition = {}
    _dispose(disposition, units[0], "ignored-with-reason", reason="fixture")
    with pytest.raises(ValueError, match="duplicate raw-unit consumption"):
        _dispose(disposition, units[0], "ignored-with-reason", reason="fixture")


def test_p2_2_5_annotations_are_byte_semantically_unchanged() -> None:
    golden = json.loads((ROOT / "tests/fixtures/full/golden-p3-summary.json").read_text(encoding="utf-8"))
    overrides = load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")
    p2_ids = {
        f"ipg-ann-{item['id'].removeprefix('pilot-ann-')}"
        for item in overrides["applied"]
        if item.get("kind") == "publication-annotation-anchor"
    }
    chapter = load_yaml(RELEASE_DIR / "chapter-02.yaml")
    annotations = [item for item in chapter["publicationAnnotations"] if item["id"] in p2_ids]
    assert len(annotations) == golden["p2Annotations"]["annotations"] == 12
    assert sum(len(group["blocks"]) for item in annotations for group in item["groups"]) == 14
    assert _canonical_hash(annotations) == golden["p2Annotations"]["canonicalSha256"]


def test_p3_registry_prefix_and_official_structure_are_frozen() -> None:
    parsed, reconciled, result = _p4()
    golden = json.loads((ROOT / "tests/fixtures/full/golden-p3-summary.json").read_text(encoding="utf-8"))
    registry = result["registry"]
    assert len(registry["entries"]) == 1976
    assert _canonical_hash(registry["entries"][:649]) == golden["p3Registry"]["canonicalSha256"]
    nodes = list(walk_nodes([reconciled["document"]]))
    assert [sum(kind == wanted for kind, _ in nodes) for wanted in ("section", "component", "group", "block")] == [36, 110, 124, 338]
    assert reconciled["reconciliation"]["resolvedRegistryIdCount"] == 608
    assert reconciled["reconciliation"]["findings"] == []
    assert parsed["coverage"]["classifiedLineCount"] == parsed["coverage"]["lineCount"] == 1050
    assert parsed["coverage"]["unclassifiedLineCount"] == 0


def test_all_translated_official_blocks_and_annotations_retain_provenance() -> None:
    for document in _documents():
        assert validate_schema(document, ROOT / "schema/ipg-source.schema.json") == []
        for kind, node in walk_nodes([document]):
            if kind == "block":
                assert node["officialPdfUnits"]
                if node["text"]["zh"]:
                    assert node["legacyRawUnits"]
                    assert node["text"]["zh"] != node["text"]["en"]
        for annotation in document["publicationAnnotations"]:
            for group in annotation["groups"]:
                for block in group["blocks"]:
                    assert block["sourceRawUnits"] and block["text"]["en"] and block["text"]["zh"]


def test_full_source_manifest_order_and_scope() -> None:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    assert manifest["documents"] == [
        "front-matter.yaml", "chapter-01.yaml", "chapter-02.yaml", "chapter-03.yaml",
        "chapter-04.yaml", "appendix-a.yaml", "appendix-b.yaml",
    ]
    assert manifest["scope"]["officialContent"]["mode"] == "full-document"
    assert manifest["scope"]["publicationAnnotations"] == {
        "mode": "full-document", "includedSections": ["full-document"],
        "deferredGroups": 0, "deferredRawUnits": 0,
    }
    assert manifest["publishable"] is False


def test_candidate_passes_and_release_fails_only_readiness_gates() -> None:
    _, _, result = _p4()
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = _documents()
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    migration = {"coverage": result["coverage"], "findings": result["findings"]}
    candidate = validate_release(
        profile="candidate", manifest=manifest, documents=documents,
        display_values=display, migration_report=migration, review_ledger=None,
    )
    release = validate_release(
        profile="release", manifest=manifest, documents=documents,
        display_values=display, migration_report=migration, review_ledger=None,
    )
    assert candidate["valid"] is True
    assert release["valid"] is False and release["structuralFindings"] == []
    assert release["readinessFindingCounts"] == {
        "annotation-license-pending": 1,
        "manifest-not-publishable": 1,
        "missing-review-ledger": 1,
        "missing-translation": 12,
    }


def test_two_full_candidate_builds_are_byte_identical_and_hash_external(tmp_path: Path) -> None:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = _documents()
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    first, second = tmp_path / "first", tmp_path / "second"
    build_outputs(first, manifest, documents, display, candidate=True, profile="candidate")
    build_outputs(second, manifest, documents, display, candidate=True, profile="candidate")
    for filename in ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()
    rules = (first / "rules.json").read_bytes()
    digest = hashlib.sha256(rules).hexdigest().encode()
    assert digest not in rules and b"rulesSha256" not in rules
    assert (first / "IPG.md").read_text(encoding="utf-8").splitlines()[1].startswith(
        "> **全文迁移候选版：不得发布。**"
    )
