import copy
import json

import pytest

from ipg_pipeline.builder import VERSION_NOTES_PATH, build_outputs, read_version_notes
from ipg_pipeline.core import ROOT, load_yaml, validate_schema
from ipg_pipeline.omegat import collect_units
from ipg_pipeline.p4 import RELEASE_DIR


def _inputs():
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [(name, load_yaml(RELEASE_DIR / name)) for name in manifest["documents"]]
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    return manifest, documents, display


def test_shared_notes_preserve_legacy_text_and_do_not_enter_omegat(tmp_path):
    old = (ROOT / "AIPG_2025.md").read_text(encoding="utf-8").split("# 目录", 1)[0]
    notes_path = tmp_path / VERSION_NOTES_PATH
    notes_path.parent.mkdir(parents=True)
    notes_path.write_text(old, encoding="utf-8", newline="\n")
    assert read_version_notes(tmp_path) == old.strip()
    _, documents, display = _inputs()
    units = collect_units(documents, display)
    assert len(units) == 1007
    assert not any(unit["id"] == "ipg-version-notes" for unit in units)


def test_edited_notes_reach_both_deterministic_outputs_without_altering_official_tree(tmp_path):
    manifest, documents, display = _inputs()
    notes_path = tmp_path / VERSION_NOTES_PATH
    notes_path.parent.mkdir(parents=True)
    notes_path.write_text("# 版本说明\r\n\r\n本地修订说明。\r\n", encoding="utf-8", newline="")
    reports = []
    for name in ("first", "second"):
        reports.append(build_outputs(
            tmp_path / name, manifest, [document for _, document in documents], display,
            candidate=True, profile="candidate", source_root=tmp_path,
        ))
    for name in ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()
    markdown = (tmp_path / "first/IPG.md").read_text(encoding="utf-8")
    rules = json.loads((tmp_path / "first/rules.json").read_text(encoding="utf-8"))
    assert markdown.startswith("<!-- CANDIDATE: NOT FOR RELEASE -->")
    assert markdown.index("# 版本说明") < markdown.index("# 万智牌违规处理方针")
    assert rules["versionNotes"] == {"id": "ipg-version-notes", "en": "", "zh": "# 版本说明\n\n本地修订说明。"}
    assert rules["sections"] == [section for _, document in documents for section in document["sections"]]
    assert validate_schema(rules, ROOT / "schema/ipg-output.schema.json") == []
    assert reports[0] == reports[1]
    assert reports[0]["outputs"]["rules.json"]["sha256"] not in str(rules)

    notes_path.write_text("# 版本说明\n\n再次修订。\n", encoding="utf-8")
    build_outputs(tmp_path / "third", manifest, [d for _, d in documents], display,
                  candidate=True, profile="candidate", source_root=tmp_path)
    changed = json.loads((tmp_path / "third/rules.json").read_text(encoding="utf-8"))
    assert changed["versionNotes"]["zh"] != rules["versionNotes"]["zh"]
    assert changed["sections"] == rules["sections"]

    for fault in ("missing", "id", "en", "zh", "unknown"):
        broken = copy.deepcopy(rules)
        if fault == "missing":
            del broken["versionNotes"]
        elif fault == "unknown":
            broken["versionNotes"]["extra"] = True
        else:
            broken["versionNotes"][fault] = "invalid"
        assert validate_schema(broken, ROOT / "schema/ipg-output.schema.json")


@pytest.mark.parametrize("fault", ["missing", "wrong-heading", "toc"])
def test_invalid_notes_fail_closed_before_build_writes_output(tmp_path, fault):
    notes_path = tmp_path / VERSION_NOTES_PATH
    notes_path.parent.mkdir(parents=True)
    if fault != "missing":
        value = "# 错误标题\n" if fault == "wrong-heading" else "# 版本说明\n\n# 目录\n"
        notes_path.write_text(value, encoding="utf-8")
    manifest, documents, display = _inputs()
    with pytest.raises((OSError, ValueError)):
        build_outputs(tmp_path / "candidate", manifest, [d for _, d in documents], display,
                      candidate=True, profile="candidate", source_root=tmp_path)
    assert not (tmp_path / "candidate").exists()
