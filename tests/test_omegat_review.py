import copy
import json
import shutil

import pytest

from ipg_pipeline.cli import RELEASE_DIR, _document_tuples
from ipg_pipeline.core import ROOT, load_json, load_yaml, validate_schema
from ipg_pipeline.omegat import (
    apply_writeback,
    collect_units,
    export_project,
    parse_po,
    preview_writeback,
    validate_target_entries,
)
from ipg_pipeline.review import terminology_audit


PROJECT = ROOT / "omegat/ipg-pilot"


def test_omegat_boundaries_and_display_values_are_unique() -> None:
    mapping = load_json(PROJECT / "omegat/ipg-pilot.mapping.json")
    ids = [unit["id"] for unit in mapping["units"]]
    assert len(ids) == len(set(ids)) == 120
    display_ids = [unit_id for unit_id in ids if unit_id.startswith("display:")]
    values = load_yaml(ROOT / "src/ipg/display-values.yaml")["values"]
    assert sorted(display_ids) == sorted(f"display:{code}" for code in values)
    assert all(display_ids.count(f"display:{code}") == 1 for code in values)
    kinds = {unit["kind"] for unit in mapping["units"]}
    assert kinds == {"display-value", "title", "official-body", "publication-annotation"}
    po = (PROJECT / "source/ipg-pilot.po").read_text(encoding="utf-8")
    assert "translation-note" not in po
    assert "reviewed-modified" not in po
    assert "pdfSha256" not in po


def test_po_parser_accepts_omegat_multiline_fields(tmp_path) -> None:
    po = tmp_path / "wrapped.po"
    po.write_text(
        'msgctxt "block:test"\nmsgid "first "\n"second"\nmsgstr "甲"\n"乙"\n',
        encoding="utf-8",
    )
    assert parse_po(po) == [
        {"id": "block:test", "source": "first second", "target": "甲乙"}
    ]


def test_writeback_preview_is_one_minimal_isolated_change() -> None:
    preview = load_json(ROOT / "reports/omegat-writeback-preview.json")
    assert preview["expectedChangeCount"] == 1
    assert preview["changes"][0]["unitId"] == "display:penalty.none"
    assert load_yaml(ROOT / "src/ipg/display-values.yaml")["values"]["penalty.none"]["zh"] == "无"
    source = (PROJECT / "source/ipg-pilot.po").read_text(encoding="utf-8")
    target = (PROJECT / "target/ipg-pilot.po").read_text(encoding="utf-8")
    assert 'msgstr "无处罚"' in source
    assert 'msgstr "无"' in target


@pytest.mark.parametrize("fault", ["unknown", "duplicate", "missing", "source-change"])
def test_po_identity_faults_block_writeback(fault: str) -> None:
    mapping = load_json(PROJECT / "omegat/ipg-pilot.mapping.json")
    entries = parse_po(PROJECT / "target/ipg-pilot.po")
    broken = copy.deepcopy(entries)
    if fault == "unknown":
        broken.append({"id": "block:unknown", "source": "x", "target": "x"})
        expected = "unknown-id"
    elif fault == "duplicate":
        broken.append(copy.deepcopy(broken[0]))
        expected = "duplicate-id"
    elif fault == "missing":
        broken.pop()
        expected = "missing-id"
    else:
        broken[0]["source"] += " changed"
        expected = "source-changed"
    assert expected in {error["code"] for error in validate_target_entries(mapping, broken)}


def test_apply_blocks_expected_count_and_post_preview_file_changes(tmp_path) -> None:
    source_root = tmp_path / "source-root"
    documents, display = _document_tuples()
    units = collect_units(documents, display)
    for relative in sorted({unit["file"] for unit in units}):
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    project = tmp_path / "omegat"
    export_project(
        project,
        source_root,
        documents,
        display,
        ROOT / "terminology/ipg-glossary.txt",
    )
    target = project / "target/ipg-pilot.po"
    target.write_text(
        target.read_text(encoding="utf-8").replace('msgstr "无"', 'msgstr "无处罚"', 1),
        encoding="utf-8",
        newline="\n",
    )
    candidate = source_root / "candidate"
    preview = preview_writeback(project, source_root, candidate)
    assert preview["expectedChangeCount"] == 1
    with pytest.raises(ValueError, match="expected change count"):
        apply_writeback(
            project, source_root, candidate, preview, expected_change_count=2
        )
    target_bytes = target.read_bytes()
    target.write_bytes(target_bytes + b"\n")
    with pytest.raises(ValueError, match="target PO changed after preview"):
        apply_writeback(
            project, source_root, candidate, preview, expected_change_count=1
        )
    target.write_bytes(target_bytes)
    candidate_file = candidate / "src/ipg/display-values.yaml"
    candidate_bytes = candidate_file.read_bytes()
    candidate_file.write_bytes(candidate_bytes + b"\n")
    with pytest.raises(ValueError, match="candidate file changed after preview"):
        apply_writeback(
            project, source_root, candidate, preview, expected_change_count=1
        )
    candidate_file.write_bytes(candidate_bytes)
    changed_file = source_root / "src/ipg/display-values.yaml"
    changed_file.write_text(
        changed_file.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="source file changed after preview"):
        apply_writeback(
            project, source_root, candidate, preview, expected_change_count=1
        )


def test_translation_notes_content_and_review_ledger_are_isolated() -> None:
    notes = load_json(ROOT / "review/translation-notes.json")
    ledger = load_json(ROOT / "review/review-ledger.json")
    assert validate_schema(notes, ROOT / "schema/translation-notes.schema.json") == []
    assert validate_schema(ledger, ROOT / "schema/review-ledger.schema.json") == []
    assert {note["stale"] for note in notes["notes"]} == {False, True}
    statuses = {entry["status"] for entry in ledger["entries"]}
    assert statuses == {"unreviewed", "reviewed-unchanged", "reviewed-modified", "stale"}
    unique_note = "保留“涵盖大多数”措辞；这是修改理由记录，不是发布注解。"
    published = "\n".join(
        [
            (ROOT / "dist/IPG.md").read_text(encoding="utf-8"),
            (ROOT / "dist/rules.json").read_text(encoding="utf-8"),
            *(path.read_text(encoding="utf-8") for path in RELEASE_DIR.glob("*.yaml")),
        ]
    )
    assert unique_note not in published
    assert "translationNotes" not in published
    assert "reviewed-modified" not in published
    assert "publicationAnnotations" in (ROOT / "dist/rules.json").read_text(encoding="utf-8")
    assert unique_note not in json.dumps(ledger, ensure_ascii=False)


def test_terminology_audit_is_advisory_even_for_bad_glossary(tmp_path) -> None:
    bad = tmp_path / "bad-glossary.txt"
    bad.write_text(
        "bad-row-without-tab\nGame Rule Violation\t违反游戏规则\n",
        encoding="utf-8",
    )
    documents, display = _document_tuples()
    report = terminology_audit(bad, collect_units(documents, display))
    assert report["nonBlocking"] is True
    assert report["glossaryIssueCount"] == 1
    assert report["findingCount"] >= 1
