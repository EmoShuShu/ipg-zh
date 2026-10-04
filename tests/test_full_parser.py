from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from ipg_pipeline.builder import build_outputs, render_markdown
from ipg_pipeline.core import ROOT, load_yaml, validate_schema, walk_nodes
from ipg_pipeline.full_parser import FullParseError, extract_full_pdf, parse_full_extraction
from ipg_pipeline.p3 import OFFICIAL_PDF, p3_manifest
from ipg_pipeline.reconcile import reconcile_full_document


P2_RELEASE = ROOT / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001"


@pytest.fixture(scope="module")
def extraction() -> dict:
    return extract_full_pdf(OFFICIAL_PDF)


@pytest.fixture(scope="module")
def parsed(extraction: dict) -> dict:
    return parse_full_extraction(copy.deepcopy(extraction))


@pytest.fixture(scope="module")
def reconciled(parsed: dict) -> dict:
    previous = [
        load_yaml(P2_RELEASE / name)
        for name in ("chapter-02.yaml", "appendix-a.yaml", "appendix-b.yaml")
    ]
    return reconcile_full_document(
        parsed,
        previous,
        load_yaml(ROOT / "src/ipg/id-registry.yaml"),
        version="ipg-2024-09-23-p3-test",
    )


@pytest.fixture(scope="module")
def full_candidate(tmp_path_factory: pytest.TempPathFactory, reconciled: dict) -> tuple[str, dict]:
    output = tmp_path_factory.mktemp("full-candidate")
    build_outputs(
        output,
        p3_manifest(),
        [reconciled["document"]],
        load_yaml(ROOT / "src/ipg/display-values.yaml"),
        candidate=True,
        profile="candidate",
    )
    return (
        (output / "IPG.md").read_text(encoding="utf-8"),
        json.loads((output / "rules.json").read_text(encoding="utf-8")),
    )


def _codes(error: pytest.ExceptionInfo[FullParseError]) -> set[str]:
    return {finding["code"] for finding in error.value.findings}


def _remove_line(extraction: dict, *, page: int, startswith: str) -> None:
    extraction["lines"] = [
        line
        for line in extraction["lines"]
        if not (line["page"] == page and line["text"].startswith(startswith))
    ]


def test_full_structure_includes_front_matter_penalty_definitions_and_23_infractions(parsed: dict) -> None:
    sections = parsed["document"]["sections"]
    by_number = {section["number"]: section for section in sections}
    assert [by_number[key]["title"]["en"] for key in ("introduction", "framework")] == [
        "Introduction",
        "Framework of this Document",
    ]
    assert [component["labelCode"] for component in by_number["1.1"]["components"] if component["role"] == "penalty-definition"] == [
        "penalty.warning",
        "penalty.game-loss",
        "penalty.match-loss",
        "penalty.disqualification",
    ]
    infractions = [section for section in sections if section["kind"] == "infraction"]
    assert len(infractions) == 23
    assert all(section.get("penaltyCode") for section in infractions)


def test_representative_component_combinations_and_upgrade_downgrade(parsed: dict) -> None:
    by_number = {section["number"]: section for section in parsed["document"]["sections"]}
    assert [component["role"] for component in by_number["2.5"]["components"]] == [
        "definition", "examples", "philosophy", "additional-remedy"
    ]
    assert "downgrade" in [component["role"] for component in by_number["3.1"]["components"]]
    assert "upgrade" in [component["role"] for component in by_number["3.5"]["components"]]
    assert [component["role"] for component in by_number["4.7"]["components"]] == ["definition", "examples"]


def test_cross_page_paragraphs_and_lists_preserve_structure(parsed: dict) -> None:
    sections = parsed["document"]["sections"]
    blocks = [node for kind, node in walk_nodes([{"sections": sections}]) if kind == "block"]
    assert any(len(block["officialPdfUnits"]) > 1 for block in blocks)
    section_4_8 = next(section for section in sections if section["number"] == "4.8")
    examples = next(component for component in section_4_8["components"] if component["role"] == "examples")
    pages = {
        unit["page"]
        for group in examples["groups"]
        for block in group["blocks"]
        for unit in block["officialPdfUnits"]
    }
    assert pages == {28, 29}


def test_appendix_a_all_rows_reference_body_and_match_penalty(parsed: dict) -> None:
    sections = parsed["document"]["sections"]
    by_temp_id = {section["id"]: section for section in sections}
    appendix = next(section for section in sections if section["number"] == "A")
    rows = appendix["components"][0]["groups"][0]["blocks"]
    assert len(rows) == 23
    for row in rows:
        assert by_temp_id[row["referenceId"]]["penaltyCode"] == row["displayCode"]


def test_appendix_b_dates_entries_and_rule_references(parsed: dict) -> None:
    appendix = next(section for section in parsed["document"]["sections"] if section["number"] == "B")
    groups = appendix["components"][0]["groups"]
    assert [(group["date"], len(group["blocks"])) for group in groups] == [
        ("September 23, 2024", 5),
        ("April 15, 2024", 6),
        ("February 2, 2024", 1),
        ("November 13, 2023", 2),
        ("September 4, 2023", 3),
    ]
    known = {section["number"] for section in parsed["document"]["sections"]}
    assert all(
        reference in known
        for group in groups
        for block in group["blocks"]
        for reference in block["ruleReferences"]
    )


def test_front_matter_headings_never_expose_internal_keys(full_candidate: tuple[str, dict], reconciled: dict) -> None:
    markdown, _ = full_candidate
    assert "### introduction" not in markdown.casefold()
    assert "### framework" not in markdown.casefold()
    assert "## **[CANDIDATE 缺译]** Introduction" in markdown
    assert "## **[CANDIDATE 缺译]** Framework of this Document" in markdown

    translated = copy.deepcopy(reconciled["document"])
    translated["sections"][0]["title"]["zh"] = "引言"
    translated_markdown = render_markdown(
        p3_manifest(),
        [translated],
        load_yaml(ROOT / "src/ipg/display-values.yaml"),
        candidate=True,
    )
    assert "## 引言 (Introduction)" in translated_markdown
    assert "## introduction" not in translated_markdown.casefold()


def test_appendix_b_dates_render_in_persisted_order_with_entry_counts(full_candidate: tuple[str, dict]) -> None:
    markdown, _ = full_candidate
    expected = [
        ("September 23, 2024", 5),
        ("April 15, 2024", 6),
        ("February 2, 2024", 1),
        ("November 13, 2023", 2),
        ("September 4, 2023", 3),
    ]
    lines = markdown.splitlines()
    indices = [lines.index(f"### {date}") for date, _ in expected]
    assert indices == sorted(indices)
    appendix_end = len(lines)
    for index, ((_, expected_count), start) in enumerate(zip(expected, indices)):
        end = indices[index + 1] if index + 1 < len(indices) else appendix_end
        assert sum(line.startswith("- ") for line in lines[start + 1 : end]) == expected_count


def test_appendix_component_titles_are_not_repeated(full_candidate: tuple[str, dict]) -> None:
    markdown, _ = full_candidate
    assert "#### 处罚快速查询" not in markdown
    assert "#### 与之前版本的更动" not in markdown


def test_rules_json_preserves_complete_appendix_date_groups(full_candidate: tuple[str, dict]) -> None:
    _, rules = full_candidate
    appendix = next(section for section in rules["sections"] if section["number"] == "B")
    groups = appendix["components"][0]["groups"]
    assert [(group["date"], len(group["blocks"])) for group in groups] == [
        ("September 23, 2024", 5),
        ("April 15, 2024", 6),
        ("February 2, 2024", 1),
        ("November 13, 2023", 2),
        ("September 4, 2023", 3),
    ]


def test_output_schema_rejects_invalid_section_id(full_candidate: tuple[str, dict]) -> None:
    _, rules = full_candidate
    broken = copy.deepcopy(rules)
    broken["sections"][0]["id"] = "not-an-ipg-id"
    assert validate_schema(broken, ROOT / "schema/ipg-output.schema.json")


def test_output_schema_rejects_block_without_text(full_candidate: tuple[str, dict]) -> None:
    _, rules = full_candidate
    broken = copy.deepcopy(rules)
    del broken["sections"][0]["components"][0]["groups"][0]["blocks"][0]["text"]
    assert validate_schema(broken, ROOT / "schema/ipg-output.schema.json")


@pytest.mark.parametrize("damage", ["anchor", "annotation-block"])
def test_output_schema_rejects_invalid_publication_annotation(
    full_candidate: tuple[str, dict], damage: str
) -> None:
    _, rules = full_candidate
    broken = copy.deepcopy(rules)
    annotation = broken["publicationAnnotations"][0]
    if damage == "anchor":
        annotation["anchor"]["type"] = "paragraph"
    else:
        del annotation["groups"][0]["blocks"][0]["text"]
    assert validate_schema(broken, ROOT / "schema/ipg-output.schema.json")


@pytest.mark.parametrize("damage", ["date", "block-type"])
def test_output_schema_rejects_invalid_change_list(full_candidate: tuple[str, dict], damage: str) -> None:
    _, rules = full_candidate
    broken = copy.deepcopy(rules)
    appendix = next(section for section in broken["sections"] if section["number"] == "B")
    group = appendix["components"][0]["groups"][0]
    if damage == "date":
        group["date"] = "2024/09/23"
    else:
        group["blocks"][0]["type"] = "paragraph"
    assert validate_schema(broken, ROOT / "schema/ipg-output.schema.json")


def test_every_official_block_has_hash_page_bbox_and_extraction_unit(parsed: dict) -> None:
    blocks = [node for kind, node in walk_nodes([parsed["document"]]) if kind == "block"]
    assert blocks
    for block in blocks:
        assert block["officialPdfUnits"]
        for unit in block["officialPdfUnits"]:
            assert unit["pdfSha256"] == parsed["pdf"]["sha256"]
            assert unit["page"] >= 1
            assert len(unit["bbox"]) == 4
            assert unit["extractionUnit"].startswith("extract-p")
    assert parsed["coverage"]["unclassifiedLineCount"] == 0
    assert parsed["coverage"]["classifiedLineCount"] == parsed["coverage"]["lineCount"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda item: item["pdf"].update(sha256="0" * 64), "pdf-hash-mismatch"),
        (lambda item: _remove_line(item, page=7, startswith="2.1."), "toc-section-missing-from-body"),
        (lambda item: _remove_line(item, page=2, startswith="2.1."), "body-heading-missing-from-toc"),
    ],
)
def test_full_parser_fails_closed_for_hash_and_toc_body_mismatch(extraction: dict, mutation, expected: str) -> None:
    changed = copy.deepcopy(extraction)
    mutation(changed)
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(changed)
    assert expected in _codes(error)


def test_duplicate_subsection_fails_closed(extraction: dict) -> None:
    changed = copy.deepcopy(extraction)
    index = next(i for i, line in enumerate(changed["lines"]) if line["page"] == 7 and line["text"].startswith("2.1."))
    duplicate = {**changed["lines"][index], "id": "extract-synthetic-duplicate"}
    changed["lines"].insert(index + 1, duplicate)
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(changed)
    assert "duplicate-body-section" in _codes(error)


def test_section_missing_from_both_toc_and_body_still_fails_expected_structure(extraction: dict) -> None:
    changed = copy.deepcopy(extraction)
    _remove_line(changed, page=2, startswith="1.5.")
    _remove_line(changed, page=6, startswith="1.5.")
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(changed)
    assert "toc-missing-expected-section" in _codes(error)


def test_unrecognized_role_and_unclassified_text_fail_closed(extraction: dict) -> None:
    role_changed = copy.deepcopy(extraction)
    role = next(line for line in role_changed["lines"] if line["page"] == 7 and line["text"] == "Definition")
    role["text"] = "Unexpected Role"
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(role_changed)
    assert "unrecognized-role-heading" in _codes(error)

    unclassified = copy.deepcopy(extraction)
    first = next(i for i, line in enumerate(unclassified["lines"]) if line["page"] == 3)
    unclassified["lines"].insert(
        first,
        {"id": "extract-synthetic-unclassified", "page": 3, "text": "Orphan body text", "x0": 72, "top": 50, "x1": 150, "bottom": 60, "bold": False},
    )
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(unclassified)
    assert "unclassified-body-candidates" in _codes(error)


def test_wrong_penalty_box_fails_appendix_cross_check(extraction: dict) -> None:
    changed = copy.deepcopy(extraction)
    heading = next(line for line in changed["lines"] if line["page"] == 13 and line["text"].startswith("2.5."))
    heading["text"] = heading["text"].removesuffix("Warning") + "Game Loss"
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(changed)
    assert "appendix-a-penalty-mismatch" in _codes(error)


def test_toc_title_and_page_must_match_body(extraction: dict) -> None:
    title_changed = copy.deepcopy(extraction)
    heading = next(line for line in title_changed["lines"] if line["page"] == 13 and line["text"].startswith("2.5."))
    heading["text"] = heading["text"].replace("Game Rule Violation", "Game Rules Violation")
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(title_changed)
    assert "toc-body-title-mismatch" in _codes(error)

    page_changed = copy.deepcopy(extraction)
    toc_line = next(line for line in page_changed["lines"] if line["page"] == 2 and line["text"].startswith("2.5."))
    toc_line["text"] = toc_line["text"].removesuffix("13") + "12"
    with pytest.raises(FullParseError) as error:
        parse_full_extraction(page_changed)
    assert "toc-body-page-mismatch" in _codes(error)


def test_reconciliation_preserves_every_p2_chapter_2_id(reconciled: dict) -> None:
    prior = load_yaml(P2_RELEASE / "chapter-02.yaml")
    prior_ids = {node["id"] for _, node in walk_nodes([prior])}
    full_ids = {node["id"] for _, node in walk_nodes([reconciled["document"]])}
    assert reconciled["reconciliation"]["findings"] == []
    assert prior_ids <= full_ids


def test_added_scope_has_blank_chinese_and_never_copies_english(reconciled: dict) -> None:
    for section in reconciled["document"]["sections"]:
        if section["number"] in {"A", "B"} or section["number"] == "2" or section["number"].startswith("2."):
            continue
        assert section["title"]["zh"] == ""
        for kind, node in walk_nodes([{"sections": [section]}]):
            if kind == "block":
                assert node["text"]["zh"] == ""
                assert node["text"]["zh"] != node["text"]["en"]


def test_source_output_schema_v1_and_full_build_are_deterministic(tmp_path: Path, reconciled: dict) -> None:
    document = reconciled["document"]
    manifest = p3_manifest()
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    assert validate_schema(document, ROOT / "schema/ipg-source.schema.json") == []
    assert validate_schema(manifest, ROOT / "schema/ipg-manifest.schema.json") == []
    first, second = tmp_path / "first", tmp_path / "second"
    build_outputs(first, manifest, [document], display, candidate=True, profile="candidate")
    build_outputs(second, manifest, [document], display, candidate=True, profile="candidate")
    for name in ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    rules = json.loads((first / "rules.json").read_text(encoding="utf-8"))
    assert validate_schema(rules, ROOT / "schema/ipg-output.schema.json") == []
    assert rules["scope"]["officialContent"]["mode"] == "full-document"
    assert rules["scope"]["publicationAnnotations"]["mode"] == "pilot"
    assert rules["publishable"] is False


def test_two_complete_parses_are_byte_identical(extraction: dict) -> None:
    first = parse_full_extraction(copy.deepcopy(extraction))
    second = parse_full_extraction(copy.deepcopy(extraction))
    encoded = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert encoded(first) == encoded(second)


def test_role_counts_are_content_derived_not_assumed(parsed: dict) -> None:
    counts = Counter(
        component["role"]
        for section in parsed["document"]["sections"]
        for component in section["components"]
    )
    assert counts["definition"] == 23
    assert counts["examples"] == 23
    assert counts["philosophy"] == 21
    assert counts["additional-remedy"] == 16
    assert counts["upgrade"] == 9
    assert counts["downgrade"] == 2


def test_compact_p3_golden_summary(parsed: dict, reconciled: dict) -> None:
    golden = json.loads((ROOT / "tests/fixtures/full/golden-p3-summary.json").read_text(encoding="utf-8"))
    document = reconciled["document"]
    sections = document["sections"]
    nodes = list(walk_nodes([document]))
    assert golden["officialPdfSha256"] == parsed["pdf"]["sha256"]
    assert golden["pages"] == parsed["pdf"]["pages"]
    assert golden["pdfTextLines"] == parsed["coverage"]["lineCount"]
    assert golden["sections"] == dict(Counter(section["kind"] for section in sections))
    assert golden["components"] == sum(kind == "component" for kind, _ in nodes)
    assert golden["groups"] == sum(kind == "group" for kind, _ in nodes)
    assert golden["blocks"] == sum(kind == "block" for kind, _ in nodes)
    assert golden["roles"] == dict(
        Counter(component["role"] for section in sections for component in section["components"])
    )
    assert golden["unclassifiedText"] == parsed["coverage"]["unclassifiedLineCount"]
    assert golden["reconciliationFindings"] == len(reconciled["reconciliation"]["findings"])
