from __future__ import annotations

import copy
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ipg_pipeline import full_review
from ipg_pipeline.builder import render_markdown
from ipg_pipeline.core import ROOT, load_yaml, sha256_text, walk_nodes
from ipg_pipeline.omegat import collect_units, parse_po, render_po
from ipg_pipeline.p4 import LEGACY_SOURCE, RELEASE_DIR
from ipg_pipeline.migration import tokenize_legacy
from ipg_pipeline.reading import add_reading_segments, reading_events
from ipg_pipeline.review import extract_omegat_notes
from ipg_pipeline.review import build_review_ledger
from ipg_pipeline.project_upgrade import upgrade_project
from ipg_pipeline.validation import validate_release

# Reuse the full workflow's isolated repository/project fixtures.
from tests.test_full_review_workflow import isolated_repo, project, _create_target, _write_po


def _chapter4():
    return load_yaml(RELEASE_DIR / "chapter-04.yaml")


def _example(document):
    return next(block for kind, block in walk_nodes([document]) if block["id"] == "ipg-b000139")


def test_launcher_has_crlf_and_works_outside_project(tmp_path):
    data = (ROOT / "审校助手.cmd").read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    if os.name != "nt":
        pytest.skip("native CMD launch is Windows-only")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(ROOT / "审校助手.cmd")], cwd=tmp_path,
                            input="4\n", encoding="utf-8", capture_output=True, timeout=30)
    assert result.returncode == 0
    assert "IPG 全文审校助手" in result.stdout
    assert not result.stderr


def test_po_has_no_translatable_metadata_header():
    text = render_po([{"id": "block:ipg-test", "kind": "official-body", "source": "Text", "target": "译文"}])
    assert "Content-Type:" not in text
    assert 'msgid ""' not in text
    assert text.startswith("#. kind:")


def test_chapter4_bilingual_fragments_interleave_annotations_and_keep_parent():
    document = _chapter4()
    parent = _example(document)
    segments = parent["readingSegments"]
    assert len(segments) == 2
    assert segments[1]["text"]["en"] == "The Head Judge is the final arbiter on what constitutes unsporting conduct."
    assert "\n\n".join(s["text"]["zh"] for s in segments) == parent["text"]["zh"]
    assert parent["text"]["en"] == " ".join(s["text"]["en"] for s in segments)
    units = collect_units([("chapter-04.yaml", document)], {"values": {}})
    ids = [u["id"] for u in units]
    assert "block:ipg-b000139" not in ids
    start = ids.index(f"reading-segment:{segments[0]['id']}")
    sequence = units[start:start+4]
    assert [u["kind"] for u in sequence] == ["official-body", "publication-annotation", "official-body", "publication-annotation"]
    assert sequence[1]["source"].startswith("It’s important to make this clarification")
    assert sequence[3]["source"].startswith("A Floor Judge")
    manifest, _, display = full_review.full_inputs()
    markdown = render_markdown(manifest, [document], display, candidate=True)
    targets = [u["target"] for u in sequence]
    assert [markdown.index(t) for t in targets] == sorted(markdown.index(t) for t in targets)


def test_all_reading_segments_preserve_official_text_ids_and_provenance():
    _, documents, display = full_review.full_inputs()
    nodes = list(walk_nodes([d for _, d in documents]))
    assert [sum(kind == wanted for kind, _ in nodes) for wanted in ("section", "component", "group", "block")] == [36, 110, 124, 338]
    split = [b for kind, b in nodes if kind == "block" and "readingSegments" in b]
    assert len(split) == 65
    assert sum(len(b["readingSegments"]) for b in split) == 167
    for block in split:
        assert block["officialPdfUnits"]
        assert "\n\n".join(s["text"]["zh"] for s in block["readingSegments"]) == block["text"]["zh"]
        assert " ".join(s["text"]["en"] for s in block["readingSegments"]) == block["text"]["en"]
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    ids = {entry["id"] for entry in registry["entries"]}
    assert all(s["id"] in ids for b in split for s in b["readingSegments"])
    assert len(collect_units(documents, display)) == 1007


def test_segmentation_is_reproducible_and_does_not_reallocate_ids():
    document = _chapter4()
    original = copy.deepcopy(document)
    for _, block in walk_nodes([document]):
        block.pop("readingSegments", None)
    for annotation in document["publicationAnnotations"]:
        annotation["anchor"].pop("segmentId", None)
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    before = copy.deepcopy(registry)
    raw, _ = tokenize_legacy(LEGACY_SOURCE)
    report = add_reading_segments(document, raw, registry, load_yaml(ROOT / "src/ipg/mapping-overrides.yaml"))
    assert not report["findings"]
    assert document == original
    assert registry == before


def test_reading_continuation_does_not_repeat_list_item_marker():
    document = _chapter4()
    parent = _example(document)
    parent.update(type="list-item", marker="A")
    leaves = [node for kind, node, _ in reading_events(document) if kind == "reading-segment" and node["id"].startswith(parent["id"] + "-rs-")]
    assert leaves[0]["type"] == "list-item" and leaves[0]["marker"] == "A"
    assert leaves[1]["type"] == "paragraph" and "marker" not in leaves[1]


@pytest.mark.parametrize("fault", ["source-range", "target", "anchor", "duplicate-id"])
def test_invalid_reading_layout_blocks_candidate(fault, monkeypatch):
    document = _chapter4()
    parent = _example(document)
    if fault == "source-range":
        parent["readingSegments"][0]["sourceRange"][1] -= 1
    elif fault == "target":
        parent["readingSegments"][0]["text"]["zh"] += "误改"
    elif fault == "anchor":
        next(a for a in document["publicationAnnotations"] if "segmentId" in a["anchor"])["anchor"]["segmentId"] = "ipg-no-rs-000000000000"
    else:
        parent["readingSegments"][1]["id"] = parent["readingSegments"][0]["id"]
    manifest, _, display = full_review.full_inputs()
    report = validate_release(profile="candidate", manifest=manifest, documents=[document], display_values=display,
                              migration_report={"coverage": {"rawUnitCount": 0, "disposedUnitCount": 0}})
    assert not report["valid"]
    monkeypatch.setattr(full_review, "full_inputs", lambda: (manifest, [("chapter-04.yaml", document)], display))
    with pytest.raises(ValueError, match="structural validation"):
        full_review.validate_repository_inputs()


def test_ambiguous_boundary_stays_unsplit_with_finding():
    document = _chapter4()
    parent = _example(document)
    parent.pop("readingSegments")
    parent["text"]["en"] = "Unrelated official text."
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    raw, _ = tokenize_legacy(LEGACY_SOURCE)
    report = add_reading_segments(document, raw, registry, {})
    assert any(f["targetId"] == parent["id"] for f in report["findings"])
    assert "readingSegments" not in parent


def test_segment_writeback_only_updates_segment_and_derived_parent(isolated_repo):
    root, project = isolated_repo
    full_review.prepare_full_project(); _create_target(project)
    path = project / "target/chapter-04.po"
    entries = parse_po(path)
    item = next(e for e in entries if e["id"].startswith("reading-segment:ipg-b000139-"))
    item["target"] += "测试"
    _write_po(path, entries)
    candidate = project.parent / "candidate"
    preview = full_review.preview_full_writeback(["chapter-04.po"], candidate)
    assert preview["actualChangeCount"] == 1
    assert len(preview["derivedChanges"]) == 1
    assert preview["expectedYamlScalarChangeCount"] == 2
    pointer = preview["changes"][0]["pointer"]
    relative = preview["changes"][0]["file"]
    before = load_yaml(root / relative)
    full_review.apply_full_writeback(preview, candidate)
    after = load_yaml(root / relative)
    parent_pointer = tuple(pointer[:pointer.index("readingSegments")])
    assert full_review._leaf_differences(before, after) == {tuple(pointer), (*parent_pointer, "text", "zh")}
    parent = _example(after)
    assert parent["text"]["zh"] == "\n\n".join(s["text"]["zh"] for s in parent["readingSegments"])
    assert full_review.prepare_full_project()["created"] is False


def test_actual_omegat_lang_and_path_note_identity(tmp_path):
    path = tmp_path / "real.tmx"
    path.write_text('<tmx><body><tu><prop type="path">block:ipg-a</prop><note>修改理由</note>'
                    '<tuv lang="en-US"><seg>Text</seg></tuv><tuv lang="zh-CN"><seg>译文</seg></tuv>'
                    '</tu></body></tmx>', encoding="utf-8")
    notes = extract_omegat_notes(path, [{"id": "block:ipg-a", "source": "Text", "target": "译文"}],
                                release_id="test", translation_revision="test", recorded_at="2026-10-05T12:00:00+08:00")
    assert notes["notes"][0]["unitId"] == "block:ipg-a"
    assert notes["notes"][0]["stale"] is False


def test_po_interleaves_all_anchor_levels_and_uses_persistent_order():
    document = _chapter4()
    section = document["sections"][0]
    component = section["components"][0]
    group = component["groups"][0]
    block = group["blocks"][0]
    template = copy.deepcopy(document["publicationAnnotations"][0])
    annotations = []
    for index, (kind, node, position) in enumerate([
        ("section", section, "before"), ("section", section, "inside-start"),
        ("component", component, "inside-start"), ("group", group, "inside-start"),
        ("block", block, "after"), ("block", block, "after"),
        ("group", group, "inside-end"), ("component", component, "inside-end"),
        ("section", section, "inside-end"), ("section", section, "after"),
    ]):
        annotation = copy.deepcopy(template)
        annotation.update(id=f"ipg-ann-order-{index}", anchor={"type": kind, "id": node["id"]}, position=position, order=index)
        annotation["groups"] = [{"id": f"ipg-ann-order-{index}-g01", "kind": "paragraphs", "blocks": [{"id": f"ipg-ann-order-{index}-b01", "type": "paragraph", "text": {"en": f"Annotation {index}", "zh": f"注解{index}"}}]}]
        annotations.append(annotation)
    document["sections"] = [section]
    document["publicationAnnotations"] = list(reversed(annotations))
    units = collect_units([("test.yaml", document)], {"values": {}})
    order = [u["source"] for u in units]
    assert order[0] == "Annotation 0"
    assert [value for value in order if value.startswith("Annotation")] == [f"Annotation {i}" for i in range(10)]
    assert order.index(block["text"]["en"]) < order.index("Annotation 4") < order.index("Annotation 5")


def _make_old_project(project):
    full_review.prepare_full_project()
    mapping = json.loads(full_review.MAPPING_PATH.read_text(encoding="utf-8"))
    _, documents, _ = full_review.full_inputs()
    parents = {b["id"]: b for _, d in documents for kind, b in walk_nodes([d]) if kind == "block" and "readingSegments" in b}
    old = []; seen = set()
    for unit in mapping["units"]:
        if unit["id"].startswith("reading-segment:"):
            pointer = unit["pointer"]
            parent = next(b for b in parents.values() if any(s["id"] == unit["id"].split(":", 1)[1] for s in b["readingSegments"]))
            unit = {**unit, "id": f"block:{parent['id']}", "source": parent["text"]["en"], "target": parent["text"]["zh"],
                    "pointer": [*pointer[:pointer.index("readingSegments")], "text", "zh"],
                    "sourceHash": sha256_text(parent["text"]["en"]), "targetHash": sha256_text(parent["text"]["zh"])}
        if unit["id"] not in seen:
            old.append(unit); seen.add(unit["id"])
    assert len(old) == 905
    mapping["units"] = old
    mapping["sourcePoHashes"] = {}
    for name in full_review.PO_ORDER:
        units = [u for u in old if u["po"] == name]
        text = render_po(units)
        (project / "source" / name).write_text(text, encoding="utf-8", newline="\n")
        (project / "target" / name).write_text(text, encoding="utf-8", newline="\n")
        mapping["sourcePoHashes"][name] = sha256_text(text)
    full_review._write_json(full_review.MAPPING_PATH, mapping)
    ledger = build_review_ledger(old, [], release_id=mapping["releaseId"], translation_revision=mapping["translationRevision"])
    full_review._write_json(full_review.REVIEW_LEDGER_PATH, ledger)
    parent = parents["ipg-b000139"]
    tmx = ET.Element("tmx"); body = ET.SubElement(tmx, "body"); tu = ET.SubElement(body, "tu")
    ET.SubElement(tu, "prop", {"type": "path"}).text = "block:ipg-b000139"
    ET.SubElement(tu, "prop", {"type": "file"}).text = "chapter-04.po"
    for language, field in [("en-US", "en"), ("zh-CN", "zh")]:
        ET.SubElement(ET.SubElement(tu, "tuv", {"lang": language}), "seg").text = parent["text"][field]
    ET.ElementTree(tmx).write(project / "omegat/project_save.tmx", encoding="UTF-8", xml_declaration=True)


def test_project_upgrade_preserves_user_target_and_original_project(isolated_repo):
    _, project = isolated_repo
    _make_old_project(project)
    path = project / "target/front-matter.po"
    entries = parse_po(path); entries[0]["target"] += "人工修改"; _write_po(path, entries)
    old_target = path.read_bytes()
    report = upgrade_project()
    backup = Path(report["backup"])
    assert (backup / "target/front-matter.po").read_bytes() == old_target
    assert parse_po(path)[0]["target"] == entries[0]["target"]
    assert report["unitCount"] == 1007 and report["convertedTmxParents"] == 1
    assert len(ET.parse(project / "omegat/project_save.tmx").findall(".//tu")) == 2
    assert full_review.prepare_full_project()["created"] is False
    ledger = json.loads(full_review.REVIEW_LEDGER_PATH.read_text(encoding="utf-8"))
    assert len(ledger["entries"]) == 1007
    assert all(e["status"] == "unreviewed" for e in ledger["entries"])


@pytest.mark.parametrize("fault", ["parent-edit", "parent-note", "source-po"])
def test_unsafe_project_upgrade_keeps_original_untouched(isolated_repo, fault):
    _, project = isolated_repo
    _make_old_project(project)
    if fault == "parent-edit":
        path = project / "target/chapter-04.po"
        entries = parse_po(path)
        next(e for e in entries if e["id"] == "block:ipg-b000139")["target"] += "人工修改"
        _write_po(path, entries)
    elif fault == "parent-note":
        path = project / "omegat/project_save.tmx"
        tree = ET.parse(path); ET.SubElement(tree.getroot().find(".//tu"), "note").text = "待关联批注"
        tree.write(path, encoding="UTF-8")
    else:
        path = project / "source/front-matter.po"
        path.write_text("changed source", encoding="utf-8")
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        upgrade_project()
    assert before == {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert not project.with_name(project.name + ".before-reading-layout").exists()
