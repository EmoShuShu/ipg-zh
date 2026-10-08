import xml.etree.ElementTree as ET

import pytest

from ipg_pipeline import full_review as workflow
from ipg_pipeline.core import load_json, sha256_text
from ipg_pipeline.omegat import minimal_yaml_update, parse_po, render_po
from scripts import correct_cpv_duplicate as correction
from test_full_review_workflow import isolated_repo, _create_target


@pytest.mark.parametrize("fail_build", [False, True])
def test_cpv_correction_preserves_work_or_rolls_back(isolated_repo, monkeypatch, fail_build):
    root, project = isolated_repo
    # Recreate the original transcription even after the real repository is repaired.
    manifest, documents, display = workflow.full_inputs()
    units = workflow.units_by_po(documents, display)
    unit = next(u for u in units["chapter-03.po"] if u["id"] == correction.UNIT_ID)
    if sha256_text(unit["source"]) != correction.OLD_HASH:
        old = unit["source"].replace(correction.SENTENCE, f"{correction.SENTENCE} {correction.SENTENCE}", 1)
        minimal_yaml_update(root / unit["file"], [*unit["pointer"][:-1], "en"], unit["source"], old)
    workflow.prepare_full_project()
    workflow.initialize_full_review_state()
    _create_target(project)
    mapping = load_json(workflow.MAPPING_PATH)
    unit = next(u for u in mapping["units"] if u["id"] == correction.UNIT_ID)
    ledger = load_json(workflow.REVIEW_LEDGER_PATH)
    for entry in ledger["entries"]:
        entry.update(status="reviewed-unchanged", reviewedAt="2026-10-05T18:22:03+08:00")
    workflow._write_json(workflow.REVIEW_LEDGER_PATH, ledger)
    target = project / "target/front-matter.po"
    entries = parse_po(target)
    entries[0]["target"] += "尚未回写"
    target.write_text(render_po([{**e, "kind": "test"} for e in entries]), encoding="utf-8")

    xml = ET.Element("tmx")
    body = ET.SubElement(xml, "body")
    for identity, source, translation in [(unit["id"], unit["source"], "尚未回写的中文"),
                                           (entries[0]["id"], entries[0]["source"], entries[0]["target"])]:
        tu = ET.SubElement(body, "tu")
        ET.SubElement(tu, "prop", {"type": "path"}).text = identity
        ET.SubElement(tu, "note").text = "保留 & 批注"
        for language, value in [("en-US", source), ("zh-CN", translation)]:
            ET.SubElement(ET.SubElement(tu, "tuv", {"lang": language}), "seg").text = value
    tmx = project / "omegat/project_save.tmx"
    ET.ElementTree(xml).write(tmx, encoding="utf-8", xml_declaration=True)
    protected = [root / unit["file"], root / "src/ipg/id-registry.yaml", workflow.REVIEW_LEDGER_PATH,
                 *(p for p in project.rglob("*") if p.is_file())]
    before = {path: path.read_bytes() for path in protected}
    if fail_build:
        def fail(*args, **kwargs):
            raise RuntimeError("injected build failure")
        monkeypatch.setattr(workflow, "build_current_candidate", fail)
        with pytest.raises(RuntimeError, match="injected build failure"):
            correction.apply_correction()
        assert {p: p.read_bytes() for p in protected} == before
        assert not (root / correction.REPORT_PATH).exists()
        return

    result = correction.apply_correction()
    after = workflow.inspect_target_project()
    current = next(u for u in after["mapping"]["units"] if u["id"] == unit["id"])
    assert current["source"].count(correction.SENTENCE) == 1
    assert current["target"] == unit["target"]
    assert after["modifiedPo"] == ["front-matter.po"]
    assert target.read_bytes() == before[target]
    assert result["reviewCounts"] == {"unreviewed": 0, "reviewed-unchanged": 1006,
                                      "reviewed-modified": 0, "stale": 1}
    expected = before[tmx].replace(unit["source"].encode(), current["source"].encode(), 1)
    assert tmx.read_bytes() == expected
    assert len(ET.parse(tmx).findall(".//note")) == 2
    with pytest.raises(ValueError, match="do not apply twice"):
        correction.apply_correction()
