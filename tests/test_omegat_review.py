import copy
import json
import shutil

import pytest

from ipg_pipeline.builder import build_outputs
from ipg_pipeline.cli import RELEASE_DIR
from ipg_pipeline.core import ROOT, load_json, load_yaml, validate_schema, walk_nodes
from ipg_pipeline.omegat import apply_writeback, collect_units, export_project, parse_po, preview_writeback, validate_target_entries
from ipg_pipeline.review import build_review_ledger, extract_translation_notes, review_status_report, terminology_audit


def _pilot_document_tuples():
    relative = "chapter-02.yaml"
    chapter = copy.deepcopy(load_yaml(RELEASE_DIR / relative))
    chapter["sections"] = [section for section in chapter["sections"] if section["number"] == "2.5"]
    ids = {node["id"] for _, node in walk_nodes([chapter])}
    chapter["publicationAnnotations"] = [
        annotation
        for annotation in chapter["publicationAnnotations"]
        if annotation["anchor"]["id"] in ids
    ]
    return [(relative, chapter)], load_yaml(ROOT / "src/ipg/display-values.yaml")


def _export(tmp_path):
    documents, display = _pilot_document_tuples()
    project = tmp_path / "omegat"
    mapping = export_project(project, ROOT, documents, display, ROOT / "terminology/ipg-glossary.txt")
    return project, mapping, documents, display


def test_omegat_boundaries_split_annotation_blocks_and_display_values_once(tmp_path) -> None:
    project, mapping, _, display = _export(tmp_path)
    ids = [unit["id"] for unit in mapping["units"]]
    assert len(ids) == len(set(ids))
    display_ids = [unit_id for unit_id in ids if unit_id.startswith("display:")]
    assert sorted(display_ids) == sorted(f"display:{code}" for code in display["values"])
    assert all(display_ids.count(f"display:{code}") == 1 for code in display["values"])
    annotation_ids = [unit_id for unit_id in ids if unit_id.startswith("annotation-block:")]
    assert len(annotation_ids) == 14
    assert sum("ipg-ann-2-5-wrong-zone" in unit_id for unit_id in annotation_ids) == 2
    po = (project / "source/ipg-pilot.po").read_text(encoding="utf-8")
    assert "translation-note" not in po and "reviewed-modified" not in po and "pdfSha256" not in po


def test_po_parser_accepts_omegat_multiline_fields(tmp_path) -> None:
    po = tmp_path / "wrapped.po"
    po.write_text('msgctxt "block:test"\nmsgid "first "\n"second"\nmsgstr "甲"\n"乙"\n', encoding="utf-8")
    assert parse_po(po) == [{"id": "block:test", "source": "first second", "target": "甲乙"}]


def test_writeback_preview_is_one_minimal_isolated_change(tmp_path) -> None:
    project, _, _, _ = _export(tmp_path)
    target = project / "target/ipg-pilot.po"
    target.write_text(target.read_text(encoding="utf-8").replace('msgstr "无"', 'msgstr "无处罚"', 1), encoding="utf-8", newline="\n")
    candidate = tmp_path / "candidate"
    preview = preview_writeback(project, ROOT, candidate)
    assert preview["expectedChangeCount"] == 1
    assert preview["changes"][0]["unitId"] == "display:penalty.none"
    assert load_yaml(ROOT / "src/ipg/display-values.yaml")["values"]["penalty.none"]["zh"] == "无"
    assert load_yaml(candidate / "src/ipg/display-values.yaml")["values"]["penalty.none"]["zh"] == "无处罚"


@pytest.mark.parametrize(("fault", "expected"), [("unknown", "unknown-id"), ("duplicate", "duplicate-id"), ("missing", "missing-id"), ("source-change", "source-changed")])
def test_po_identity_faults_block_writeback(tmp_path, fault: str, expected: str) -> None:
    project, mapping, _, _ = _export(tmp_path)
    entries = parse_po(project / "target/ipg-pilot.po")
    broken = copy.deepcopy(entries)
    if fault == "unknown": broken.append({"id": "block:unknown", "source": "x", "target": "x"})
    elif fault == "duplicate": broken.append(copy.deepcopy(broken[0]))
    elif fault == "missing": broken.pop()
    else: broken[0]["source"] += " changed"
    assert expected in {error["code"] for error in validate_target_entries(mapping, broken)}


def test_apply_blocks_count_and_changes_after_preview(tmp_path) -> None:
    source_root = tmp_path / "source-root"
    documents, display = _pilot_document_tuples(); units = collect_units(documents, display)
    for relative in sorted({unit["file"] for unit in units}):
        destination = source_root / relative; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / relative, destination)
    project = tmp_path / "omegat"; export_project(project, source_root, documents, display, ROOT / "terminology/ipg-glossary.txt")
    target = project / "target/ipg-pilot.po"; target.write_text(target.read_text(encoding="utf-8").replace('msgstr "无"', 'msgstr "无处罚"', 1), encoding="utf-8", newline="\n")
    candidate = source_root / "candidate"; preview = preview_writeback(project, source_root, candidate)
    with pytest.raises(ValueError, match="expected change count"):
        apply_writeback(project, source_root, candidate, preview, expected_change_count=2)
    target_bytes = target.read_bytes(); target.write_bytes(target_bytes + b"\n")
    with pytest.raises(ValueError, match="target PO changed"):
        apply_writeback(project, source_root, candidate, preview, expected_change_count=1)
    target.write_bytes(target_bytes)
    candidate_file = candidate / "src/ipg/display-values.yaml"; candidate_bytes = candidate_file.read_bytes(); candidate_file.write_bytes(candidate_bytes + b"\n")
    with pytest.raises(ValueError, match="candidate file changed"):
        apply_writeback(project, source_root, candidate, preview, expected_change_count=1)
    candidate_file.write_bytes(candidate_bytes)
    changed_file = source_root / "src/ipg/display-values.yaml"; changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="source file changed"):
        apply_writeback(project, source_root, candidate, preview, expected_change_count=1)


def test_translation_notes_versioning_and_target_change_staleness() -> None:
    documents, display = _pilot_document_tuples(); units = collect_units(documents, display)
    kwargs = {"release_id": "ipg-test", "translation_revision": "zh-r0001"}
    notes = extract_translation_notes(ROOT / "tests/fixtures/pilot/translation-notes.tmx", units, **kwargs)
    assert validate_schema(notes, ROOT / "schema/translation-notes.schema.json") == []
    assert [note["stale"] for note in notes["notes"]] == [False, True]
    changed = copy.deepcopy(units)
    target = next(unit for unit in changed if unit["id"] == "block:ipg-s2-5-c-definition-g01-b001")
    target["target"] += "（仅测试）"
    changed_notes = extract_translation_notes(ROOT / "tests/fixtures/pilot/translation-notes.tmx", changed, **kwargs)
    assert changed_notes["notes"][0]["stale"] is True
    assert all({"unitId", "sourceHash", "targetHash", "releaseId", "translationRevision"} <= set(note) for note in notes["notes"])


def test_translation_note_without_time_requires_explicit_parameter(tmp_path) -> None:
    tmx = tmp_path / "note.tmx"
    tmx.write_text('<?xml version="1.0"?><tmx><body><tu tuid="x"><prop type="x-translation-note">why</prop><tuv xml:lang="en"><seg>a</seg></tuv><tuv xml:lang="zh"><seg>甲</seg></tuv></tu></body></tmx>', encoding="utf-8")
    unit = [{"id": "x", "source": "a", "target": "甲"}]
    with pytest.raises(ValueError, match="provide recorded_at"):
        extract_translation_notes(tmx, unit, release_id="r", translation_revision="t")
    result = extract_translation_notes(tmx, unit, release_id="r", translation_revision="t", recorded_at="2026-10-04")
    assert result["notes"][0]["recordedAt"] == "2026-10-04"


def test_review_ledger_has_four_states_and_is_isolated(tmp_path) -> None:
    documents, display = _pilot_document_tuples(); units = collect_units(documents, display)
    actions = load_yaml(ROOT / "review/actions/zh-r0001.yaml")["actions"]
    ledger = build_review_ledger(units, actions, release_id="ipg-test", translation_revision="zh-r0001")
    assert validate_schema(ledger, ROOT / "schema/review-ledger.schema.json") == []
    assert set(entry["status"] for entry in ledger["entries"]) == {"unreviewed", "reviewed-unchanged", "reviewed-modified", "stale"}
    assert sum(review_status_report(ledger)["counts"].values()) == len(units)
    output = tmp_path / "output"; manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    build_outputs(output, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate")
    unique_note = "保留“涵盖大多数”措辞；这是修改理由记录，不是发布注解。"
    published = (output / "IPG.md").read_text(encoding="utf-8") + (output / "rules.json").read_text(encoding="utf-8") + json.dumps([item[1] for item in documents], ensure_ascii=False)
    assert unique_note not in published and "translationNotes" not in published and "reviewed-modified" not in published


def test_terminology_audit_is_advisory_even_for_bad_glossary(tmp_path) -> None:
    bad = tmp_path / "bad-glossary.txt"; bad.write_text("bad-row-without-tab\nGame Rule Violation\t不存在的受控译名\n", encoding="utf-8")
    documents, display = _pilot_document_tuples(); report = terminology_audit(bad, collect_units(documents, display))
    assert report["nonBlocking"] is True and report["glossaryIssueCount"] == 1 and report["findingCount"] >= 1
