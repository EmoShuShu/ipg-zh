import json
from copy import deepcopy

import pytest

from ipg_pipeline.cli import OFFICIAL_PDF, PARSED_JSON, RELEASE_DIR
from ipg_pipeline.core import ROOT, load_json, load_yaml, validate_schema, walk_nodes
from ipg_pipeline.migration import _consume, tokenize_legacy
from ipg_pipeline.pilot_parser import OFFICIAL_SHA256, SUPPORTED_PAGES, parse_pilot


def _documents() -> dict:
    return {
        name: load_yaml(RELEASE_DIR / name)
        for name in ("chapter-02.yaml", "appendix-a.yaml", "appendix-b.yaml")
    }


def test_pilot_parser_matches_golden_fixture() -> None:
    actual = parse_pilot(OFFICIAL_PDF)
    golden = load_json(ROOT / "tests/fixtures/pilot/golden-official-pilot.json")
    assert actual == golden
    assert actual == load_json(PARSED_JSON)
    assert tuple(SUPPORTED_PAGES) == (*range(7, 15), 30, 31)


def test_every_official_block_has_pdf_page_bbox_and_hash() -> None:
    for document in _documents().values():
        for kind, node in walk_nodes([document]):
            if kind != "block":
                continue
            source = node["officialPdfUnit"]
            assert source["pdfSha256"] == OFFICIAL_SHA256
            assert source["page"] in SUPPORTED_PAGES
            x0, top, x1, bottom = source["bbox"]
            assert 0 <= x0 < x1 <= 612
            assert 0 <= top < bottom <= 792


def test_2_5_is_complete_golden_sample() -> None:
    document = _documents()["chapter-02.yaml"]
    section = next(item for item in document["sections"] if item["number"] == "2.5")
    assert section["penaltyCode"] == "penalty.warning"
    roles = {component["role"]: component for component in section["components"]}
    assert set(roles) == {"definition", "examples", "philosophy", "additional-remedy"}
    assert len(roles["examples"]["groups"][0]["blocks"]) == 5
    remedy_groups = roles["additional-remedy"]["groups"]
    assert [group["kind"] for group in remedy_groups] == ["paragraphs", "unordered-list", "paragraphs"]
    assert len(remedy_groups[1]["blocks"]) == 5
    blocks = [node for kind, node in walk_nodes([{"sections": [section]}]) if kind == "block"]
    assert all(block["text"]["zh"] for block in blocks)


def test_appendix_samples_cover_table_and_change_log() -> None:
    documents = _documents()
    appendix_a = documents["appendix-a.yaml"]["sections"][0]
    rows = appendix_a["components"][0]["groups"][0]["blocks"]
    assert [(row["text"]["en"], row["displayCode"]) for row in rows] == [
        ("Missed Trigger", "penalty.none"),
        ("Game Rule Violation", "penalty.warning"),
        ("Tardiness", "penalty.game-loss"),
        ("Cheating", "penalty.disqualification"),
    ]
    changes = documents["appendix-b.yaml"]["sections"][0]["components"][0]["groups"][0]["blocks"]
    assert len(changes) == 5
    assert all(change["text"]["zh"] for change in changes)


def test_publication_annotations_cover_all_anchor_levels_and_legal_positions() -> None:
    annotations = _documents()["chapter-02.yaml"]["publicationAnnotations"]
    assert {item["anchor"]["type"] for item in annotations} == {"section", "component", "group", "block"}
    assert {item["position"] for item in annotations} == {"inside-start", "inside-end", "after"}
    assert all(item["text"]["en"] and item["text"]["zh"] for item in annotations)


def test_source_files_match_schema() -> None:
    schema = ROOT / "schema/ipg-source.schema.json"
    for filename, document in _documents().items():
        assert validate_schema(document, schema) == [], filename


def test_raw_tokenizer_is_lossless_and_every_unit_has_one_disposition() -> None:
    units, digest = tokenize_legacy(ROOT / "AIPG_2025.md")
    ledger = load_json(ROOT / "work/migration/raw-unit-ledger.json")
    assert digest == "acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb"
    assert len(units) == len(ledger) == len({item["rawUnitId"] for item in ledger})
    by_line = {item["line"]: item for item in ledger}
    assert by_line[3246]["disposition"] == "regression-observed-unpaired-quote-format"
    assert by_line[3248]["disposition"] == "regression-observed-unpaired-quote-format"


def test_duplicate_consumption_is_rejected() -> None:
    unit = {"id": "raw-L000001", "line": 1}
    dispositions = {}
    _consume(dispositions, unit, "mapped-official-en", "ipg-a")
    with pytest.raises(ValueError, match="duplicate raw-unit consumption"):
        _consume(dispositions, unit, "mapped-translation", "ipg-b")


def test_no_legacy_mapping_keeps_translation_empty_and_reports_finding() -> None:
    report = load_json(ROOT / "reports/migration-report.json")
    documents = _documents()
    by_id = {
        node["id"]: node
        for document in documents.values()
        for kind, node in walk_nodes([document])
        if kind == "block"
    }
    missing = [item["targetId"] for item in report["findings"] if item["code"] == "missing-translation"]
    assert missing
    assert all(by_id[target]["text"]["zh"] == "" for target in missing)
    assert all(by_id[target]["text"]["en"] != by_id[target]["text"]["zh"] for target in missing)


def test_wrong_pdf_hash_fails_before_parsing(tmp_path) -> None:
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not the official PDF")
    with pytest.raises(ValueError, match="official PDF hash mismatch"):
        parse_pilot(bad_pdf)


def test_registry_contains_every_live_node_without_duplicate_ids() -> None:
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    entries = registry["entries"]
    assert len(entries) == len({entry["id"] for entry in entries})
    active = {entry["id"] for entry in entries if entry["status"] == "active"}
    live = {
        node["id"]
        for document in _documents().values()
        for _, node in walk_nodes([document])
    }
    live |= {
        annotation["id"]
        for document in _documents().values()
        for annotation in document["publicationAnnotations"]
    }
    assert live <= active

