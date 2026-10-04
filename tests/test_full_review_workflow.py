from __future__ import annotations

import json
from pathlib import Path

import pytest

import ipg_pipeline.full_review as full_review
from ipg_pipeline.omegat import parse_po


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "omegat-ipg-full"
    monkeypatch.setattr(full_review, "PROJECT_DIR", path)
    monkeypatch.setattr(full_review, "MAPPING_PATH", path / "omegat/full-review.mapping.json")
    return path


def test_full_project_exports_exactly_eight_po_and_905_unique_units(project: Path) -> None:
    result = full_review.prepare_full_project()
    assert result["created"] is True
    assert result["unitCount"] == 905
    assert result["poCounts"] == full_review.EXPECTED_PO_COUNTS
    source_files = sorted(path.name for path in (project / "source").glob("*.po"))
    assert source_files == sorted(full_review.PO_ORDER)
    entries = {
        name: parse_po(project / "source" / name)
        for name in full_review.PO_ORDER
    }
    assert {name: len(items) for name, items in entries.items()} == full_review.EXPECTED_PO_COUNTS
    ids = [item["id"] for items in entries.values() for item in items]
    assert len(ids) == len(set(ids)) == 905
    assert sum(item["id"].startswith("display:") for item in entries["display-values.po"]) == 15
    assert sum(item["id"].startswith("annotation-block:") for items in entries.values() for item in items) == 516


def test_appendix_b_has_twelve_empty_targets_and_other_source_targets_are_preserved(project: Path) -> None:
    full_review.prepare_full_project()
    appendix = parse_po(project / "source/appendix-b.po")
    assert sum(not item["target"] for item in appendix) == 12
    mapping = json.loads((project / "omegat/full-review.mapping.json").read_text(encoding="utf-8"))
    assert len(mapping["initialMissingUnitIds"]) == 12
    assert set(mapping["initialMissingUnitIds"]) == {item["id"] for item in appendix if not item["target"]}


def test_one_project_contains_all_po_and_direct_repository_glossary(project: Path) -> None:
    full_review.prepare_full_project()
    xml = (project / "omegat.project").read_text(encoding="utf-8")
    assert "<sentenceSeg>false</sentenceSeg>" in xml
    assert "LuceneEnglishTokenizer" in xml and "LuceneSmartChineseTokenizer" in xml
    assert "../../terminology/" in xml and "ipg-glossary.txt" in xml
    assert not (project / "glossary").exists()
    assert list((project / "target").iterdir()) == []
    assert (project / "omegat/filters.xml").is_file()


def test_prepare_again_validates_without_overwriting_user_files(project: Path) -> None:
    full_review.prepare_full_project()
    source_before = {path.name: path.read_bytes() for path in (project / "source").glob("*.po")}
    target = project / "target/chapter-01.po"
    target.write_text("user target", encoding="utf-8")
    tmx = project / "omegat/project_save.tmx"
    tmx.write_text("user tmx", encoding="utf-8")
    note = project / "omegat/user-note.txt"
    note.write_text("user note", encoding="utf-8")
    result = full_review.prepare_full_project()
    assert result["created"] is False
    assert target.read_text(encoding="utf-8") == "user target"
    assert tmx.read_text(encoding="utf-8") == "user tmx"
    assert note.read_text(encoding="utf-8") == "user note"
    assert source_before == {path.name: path.read_bytes() for path in (project / "source").glob("*.po")}


def test_existing_directory_without_project_file_fails_closed(project: Path) -> None:
    project.mkdir(parents=True)
    with pytest.raises(ValueError, match="omegat.project 缺失"):
        full_review.prepare_full_project()
