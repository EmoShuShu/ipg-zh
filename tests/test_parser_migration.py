from copy import deepcopy
from functools import lru_cache

import pytest

from ipg_pipeline.cli import OFFICIAL_PDF, RELEASE_DIR
from ipg_pipeline.core import ROOT, load_json, load_yaml, validate_schema, walk_nodes
from ipg_pipeline.migration import _consume, migrate_pilot, tokenize_legacy
from ipg_pipeline.pilot_parser import OFFICIAL_SHA256, SUPPORTED_PAGES, extract_pilot, parse_pilot
from ipg_pipeline.reconcile import reconcile_pilot


def _documents() -> dict:
    documents = {
        name: load_yaml(RELEASE_DIR / name)
        for name in ("chapter-02.yaml", "appendix-a.yaml", "appendix-b.yaml")
    }
    p2_ids = {
        f"ipg-ann-{item['id'].removeprefix('pilot-ann-')}"
        for item in load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")["applied"]
        if item.get("kind") == "publication-annotation-anchor"
    }
    documents["chapter-02.yaml"] = deepcopy(documents["chapter-02.yaml"])
    documents["chapter-02.yaml"]["publicationAnnotations"] = [
        annotation
        for annotation in documents["chapter-02.yaml"]["publicationAnnotations"]
        if annotation["id"] in p2_ids
    ]
    return documents


@lru_cache(maxsize=1)
def _migration() -> dict:
    return migrate_pilot(
        parse_pilot(OFFICIAL_PDF), ROOT / "AIPG_2025.md",
        deepcopy(load_yaml(ROOT / "src/ipg/id-registry.yaml")),
        load_yaml(ROOT / "src/ipg/mapping-overrides.yaml"),
    )


def test_parser_matches_compact_golden_fixture() -> None:
    actual = parse_pilot(OFFICIAL_PDF)
    golden = load_json(ROOT / "tests/fixtures/pilot/golden-pilot-summary.json")
    counts = {name: sum(1 for kind, _ in walk_nodes([doc]) if kind == "block") for name, doc in actual["documents"].items()}
    cross_page = sum(len(node["officialPdfUnits"]) > 1 for doc in actual["documents"].values() for kind, node in walk_nodes([doc]) if kind == "block")
    assert actual["scope"] == golden["scope"]
    assert actual["pdf"] == {"sha256": golden["pdfSha256"], "pages": golden["pageCount"]}
    assert counts == golden["documentBlockCounts"]
    assert cross_page == golden["crossPageBlockCount"]
    assert tuple(SUPPORTED_PAGES) == (*range(7, 15), 30, 31)


def test_extraction_ids_are_temporary_then_reconciled_by_evidence() -> None:
    extracted = extract_pilot(OFFICIAL_PDF)
    ids = [node["id"] for doc in extracted["documents"].values() for _, node in walk_nodes([doc])]
    assert extracted["idStatus"] == "temporary-extraction-ids"
    assert ids and all(item.startswith("extract-") for item in ids)
    reconciled = reconcile_pilot(
        extracted, _documents(), load_yaml(ROOT / "src/ipg/mapping-overrides.yaml"),
        load_yaml(ROOT / "src/ipg/id-registry.yaml"),
    )
    assert reconciled["idStatus"] == "reconciled-stable-ids"
    assert reconciled["reconciliationFindings"] == []
    stable = [node["id"] for doc in reconciled["documents"].values() for _, node in walk_nodes([doc])]
    assert stable and all(item.startswith("ipg-") for item in stable)


def test_every_official_block_has_pdf_page_bbox_and_hash() -> None:
    for document in _documents().values():
        for kind, node in walk_nodes([document]):
            if kind != "block":
                continue
            assert node["officialPdfUnits"]
            for source in node["officialPdfUnits"]:
                assert source["pdfSha256"] == OFFICIAL_SHA256
                assert source["page"] in SUPPORTED_PAGES
                x0, top, x1, bottom = source["bbox"]
                assert 0 <= x0 < x1 <= 612
                assert 0 <= top < bottom <= 792


def test_2_5_is_complete_golden_sample() -> None:
    section = next(item for item in _documents()["chapter-02.yaml"]["sections"] if item["number"] == "2.5")
    roles = {component["role"]: component for component in section["components"]}
    assert section["penaltyCode"] == "penalty.warning"
    assert set(roles) == {"definition", "examples", "philosophy", "additional-remedy"}
    assert len(roles["examples"]["groups"][0]["blocks"]) == 5
    assert [group["kind"] for group in roles["additional-remedy"]["groups"]] == ["paragraphs", "unordered-list", "paragraphs"]
    blocks = [node for kind, node in walk_nodes([{"sections": [section]}]) if kind == "block"]
    assert len(blocks) == 17
    assert all(block["text"]["zh"] and block["legacyRawUnits"] for block in blocks)


def test_2_5_raw_units_have_specific_unique_dispositions() -> None:
    counts = _migration()["coverage"]["section2_5Dispositions"]
    assert counts == {
        "ignored-layout": 52, "ignored-quote-separator": 16, "mapped-bilingual-title": 1,
        "mapped-official-en": 17, "mapped-publication-annotation": 14,
        "mapped-publication-annotation-translation": 14, "mapped-structural": 5,
        "mapped-translation": 17,
    }
    assert sum(counts.values()) == 136
    assert not {"deferred-pilot-content", "unresolved-mapping"} & set(counts)


def test_publication_annotations_preserve_multiparagraph_order_and_anchor() -> None:
    annotations = _documents()["chapter-02.yaml"]["publicationAnnotations"]
    blocks = [block for item in annotations for group in item["groups"] for block in group["blocks"]]
    assert len(annotations) == 12 and len(blocks) == 14
    assert [item["order"] for item in annotations] == list(range(10, 121, 10))
    assert all(item["anchor"]["type"] == "block" and item["position"] == "after" for item in annotations)
    wrong_zone = next(item for item in annotations if item["id"] == "ipg-ann-2-5-wrong-zone")
    assert [block["sourceRawUnits"] for block in wrong_zone["groups"][0]["blocks"]] == [
        ["raw-L001795", "raw-L001799"], ["raw-L001797", "raw-L001801"]
    ]
    same_anchor = [item for item in annotations if item["anchor"]["id"] == "ipg-s2-5-c-additional-remedy-g03-b003"]
    assert [item["order"] for item in same_anchor] == [110, 120]


def test_annotation_schema_supports_all_anchor_levels_lists_and_applies_to() -> None:
    document = deepcopy(_documents()["chapter-02.yaml"])
    section = next(item for item in document["sections"] if item["number"] == "2.5")
    component, group = section["components"][0], section["components"][0]["groups"][0]
    block = group["blocks"][0]
    anchors = [("section", section["id"]), ("component", component["id"]), ("group", group["id"]), ("block", block["id"])]
    document["publicationAnnotations"] = []
    for index, (kind, target) in enumerate(anchors, 1):
        document["publicationAnnotations"].append({
            "id": f"ipg-ann-synthetic-{index}", "anchor": {"type": kind, "id": target},
            "position": "after", "order": index, "appliesTo": [block["id"]],
            "groups": [{"id": f"ipg-ann-synthetic-{index}-g01", "kind": "unordered-list" if index == 1 else "paragraphs",
                        "blocks": [{"id": f"ipg-ann-synthetic-{index}-g01-b01", "type": "list-item" if index == 1 else "paragraph",
                                    "text": {"en": "e", "zh": "中"}, "sourceRawUnits": ["raw-L000001", "raw-L000002"]}]}],
        })
    assert validate_schema(document, ROOT / "schema/ipg-source.schema.json") == []


def test_chapter_intro_alignment_uses_all_repeated_fragments() -> None:
    chapter = _migration()["documents"]["chapter-02.yaml"]
    section = next(item for item in chapter["sections"] if item["number"] == "2")
    block = next(node for kind, node in walk_nodes([{"sections": [section]}]) if kind == "block")
    assert block["legacyRawUnits"] == ["raw-L000653", "raw-L000655", "raw-L000665", "raw-L000667", "raw-L000673", "raw-L000675"]
    assert block["text"]["zh"].count("\n\n") == 2


def test_raw_tokenizer_is_lossless_and_regression_lines_are_fixed() -> None:
    units, digest = tokenize_legacy(ROOT / "AIPG_2025.md")
    ledger = _migration()["coverageLedger"]
    assert digest == "acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb"
    assert len(units) == len(ledger) == len({item["rawUnitId"] for item in ledger})
    by_line = {item["line"]: item for item in ledger}
    assert by_line[3246]["disposition"] == "regression-observed-unpaired-quote-format"
    assert by_line[3248]["disposition"] == "regression-observed-unpaired-quote-format"


def test_duplicate_consumption_is_rejected() -> None:
    unit, dispositions = {"id": "raw-L000001", "line": 1}, {}
    _consume(dispositions, unit, "mapped-official-en", "ipg-a")
    with pytest.raises(ValueError, match="duplicate raw-unit consumption"):
        _consume(dispositions, unit, "mapped-translation", "ipg-b")


def test_all_former_missing_targets_are_now_mapped() -> None:
    result = _migration()
    assert result["findingCounts"] == {}
    assert all(mapping["chineseCoverage"] == "complete" for mapping in result["blockMappings"])
    assert any(mapping.get("overrideId") == "pilot-body-2-1-delayed-copy" for mapping in result["blockMappings"])


def test_source_files_match_schema_and_registry_contains_live_ids() -> None:
    documents = _documents()
    for filename, document in documents.items():
        assert validate_schema(document, ROOT / "schema/ipg-source.schema.json") == [], filename
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    active = {entry["id"] for entry in registry["entries"] if entry["status"] == "active"}
    live = {node["id"] for document in documents.values() for _, node in walk_nodes([document])}
    for document in documents.values():
        for annotation in document["publicationAnnotations"]:
            live.add(annotation["id"])
            for group in annotation["groups"]:
                live.add(group["id"]); live.update(block["id"] for block in group["blocks"])
    assert live <= active


def test_wrong_pdf_hash_fails_before_parsing(tmp_path) -> None:
    bad_pdf = tmp_path / "bad.pdf"; bad_pdf.write_bytes(b"not the official PDF")
    with pytest.raises(ValueError, match="official PDF hash mismatch"):
        parse_pilot(bad_pdf)
