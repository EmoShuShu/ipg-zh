from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import ipg_pipeline.full_review as full_review
from ipg_pipeline.core import load_yaml
from ipg_pipeline.omegat import parse_po, render_po


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "omegat-ipg-full"
    monkeypatch.setattr(full_review, "PROJECT_DIR", path)
    monkeypatch.setattr(full_review, "MAPPING_PATH", path / "omegat/full-review.mapping.json")
    return path


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    for name in ("src", "schema", "terminology"):
        shutil.copytree(full_review.ROOT / name, root / name)
    release = root / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001"
    project = root / "outputs/omegat-ipg-full"
    monkeypatch.setattr(full_review, "ROOT", root)
    monkeypatch.setattr(full_review, "RELEASE_DIR", release)
    monkeypatch.setattr(full_review, "PROJECT_DIR", project)
    monkeypatch.setattr(full_review, "MAPPING_PATH", project / "omegat/full-review.mapping.json")
    monkeypatch.setattr(full_review, "GLOSSARY_PATH", root / "terminology/ipg-glossary.txt")
    return root, project


def _create_target(project: Path) -> None:
    for name in full_review.PO_ORDER:
        shutil.copyfile(project / "source" / name, project / "target" / name)


def _write_po(path: Path, entries: list[dict[str, str]]) -> None:
    path.write_text(
        render_po([{**entry, "kind": "test"} for entry in entries]),
        encoding="utf-8",
        newline="\n",
    )


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


@pytest.mark.parametrize(("damage", "expected"), [
    ("unknown", "unknown-id"),
    ("duplicate", "duplicate-id"),
    ("missing", "missing-id"),
    ("source", "source-changed"),
])
def test_target_identity_faults_block_preview(
    isolated_repo: tuple[Path, Path], damage: str, expected: str
) -> None:
    _, project = isolated_repo
    full_review.prepare_full_project(); _create_target(project)
    path = project / "target/chapter-01.po"
    entries = parse_po(path)
    if damage == "unknown":
        entries.append({"id": "block:unknown", "source": "x", "target": "x"})
    elif damage == "duplicate":
        entries.append(entries[0].copy())
    elif damage == "missing":
        entries.pop()
    else:
        entries[0]["source"] += " changed"
    _write_po(path, entries)
    with pytest.raises(ValueError, match=expected):
        full_review.preview_full_writeback(["chapter-01.po"], project.parent / "candidate")


def test_missing_or_extra_target_po_blocks_preview(isolated_repo: tuple[Path, Path]) -> None:
    _, project = isolated_repo
    full_review.prepare_full_project(); _create_target(project)
    (project / "target/appendix-a.po").unlink()
    with pytest.raises(ValueError, match="恰好包含 8 个文件"):
        full_review.inspect_target_project()


def test_unselected_modified_po_blocks_batch(isolated_repo: tuple[Path, Path]) -> None:
    _, project = isolated_repo
    full_review.prepare_full_project(); _create_target(project)
    path = project / "target/display-values.po"
    entries = parse_po(path); entries[0]["target"] += "测试"; _write_po(path, entries)
    with pytest.raises(ValueError, match="未选择的 PO 中存在文字修改"):
        full_review.preview_full_writeback(["chapter-01.po"], project.parent / "candidate")


def test_empty_translation_writeback_changes_only_one_zh_scalar(
    isolated_repo: tuple[Path, Path]
) -> None:
    root, project = isolated_repo
    full_review.prepare_full_project(); _create_target(project)
    target_path = project / "target/appendix-b.po"
    entries = parse_po(target_path)
    changed = next(item for item in entries if not item["target"])
    changed["target"] = "测试译文"
    _write_po(target_path, entries)
    candidate = project.parent / "candidate"
    preview = full_review.preview_full_writeback(["appendix-b.po"], candidate)
    assert preview["actualChangeCount"] == 1
    assert preview["reviewedUnitCount"] == 18
    assert preview["reviewedUnchangedCount"] == 17
    assert preview["initialMissingRemaining"] == 11
    relative = preview["changes"][0]["file"]
    before = load_yaml(root / relative)
    result = full_review.apply_full_writeback(preview, candidate)
    after = load_yaml(root / relative)
    assert result["appliedChangeCount"] == 1
    assert full_review._leaf_differences(before, after) == {tuple(preview["changes"][0]["pointer"])}


@pytest.mark.parametrize("changed", ["target", "yaml"])
def test_preview_detects_target_or_formal_yaml_change_before_apply(
    isolated_repo: tuple[Path, Path], changed: str
) -> None:
    root, project = isolated_repo
    full_review.prepare_full_project(); _create_target(project)
    candidate = project.parent / "candidate"
    preview = full_review.preview_full_writeback(["chapter-01.po"], candidate)
    if changed == "target":
        path = project / "target/chapter-01.po"
        path.write_bytes(path.read_bytes() + b"\n")
        expected = "target PO changed"
    else:
        relative = next(iter(preview["baseFiles"]))
        path = root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        expected = "formal YAML changed"
    with pytest.raises(ValueError, match=expected):
        full_review.apply_full_writeback(preview, candidate)
