import json
import copy
from pathlib import Path
import shutil
import pytest
from ipg_pipeline import promotion, full_review, maintenance
from ipg_pipeline.core import load_json, load_yaml
from ipg_pipeline.production import load_project, validate_project
from ipg_pipeline.project_context import project_context
from tests.test_official_update import repo, _check, PDF


def snapshot(root):
    def change(parsed):
        blocks = parsed["document"]["sections"][0]["components"][0]["groups"][0]["blocks"]
        extra = copy.deepcopy(blocks[0])
        extra.update(id="extract-block-new", text={"en": "TEST ONLY added paragraph", "zh": ""})
        blocks.append(extra)
    report = _check(root, payload=PDF + b" new official version", mutate=change)
    return root / report["snapshot"]


def protected(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for name in ("src", "review", "dist", "snapshots", "outputs/omegat-ipg-full")
            for p in (root / name).rglob("*") if p.is_file()}


def test_preview_cancel_and_promotion_preserve_old_versions_and_user_work(repo):
    source = snapshot(repo)
    before = protected(repo)
    preview = promotion.promote(repo, source)
    assert preview["status"] == "preview" and protected(repo) == before
    assert promotion.promote(repo, source, confirm=lambda report: False)["status"] == "cancelled"
    assert protected(repo) == before
    result = promotion.promote(repo, source, confirm=lambda report: True)
    assert result["status"] == "promoted-for-review"
    mutable = {"src/ipg/current-release.txt", "src/ipg/id-registry.yaml", "src/ipg/mapping-overrides.yaml"}
    assert all((repo / p).read_bytes() == content for p, content in before.items() if p not in mutable)
    assert load_project(repo)["manifest"]["releaseId"] == result["releaseId"]
    assert validate_project(repo, profile="candidate")["valid"]
    assert not validate_project(repo, profile="release")["valid"]
    assert "unreviewed" in {e["status"] for e in load_project(repo)["ledger"]["entries"]}
    with pytest.raises(ValueError, match="当前版本已变化"):
        promotion.promote(repo, source)


@pytest.mark.parametrize("fault", ["source", "snapshot", "interrupt", "final-validation"])
def test_changed_inputs_or_failed_promotion_never_leaves_partial_baseline(repo, monkeypatch, fault):
    source = snapshot(repo)
    before = protected(repo)
    if fault == "source":
        def confirm(report):
            path = repo / "src/ipg/version-notes.md"
            path.write_text(path.read_text() + "\nUSER EDIT\n")
            return True
        with pytest.raises(ValueError):
            promotion.promote(repo, source, confirm=confirm)
        assert (repo / "src/ipg/version-notes.md").read_text().endswith("USER EDIT\n")
        assert (repo / "src/ipg/current-release.txt").read_bytes() == before["src/ipg/current-release.txt"]
    elif fault == "snapshot":
        (source / "reports/update.json").write_text("{}")
        with pytest.raises(ValueError):
            promotion.promote(repo, source, confirm=lambda report: True)
        assert protected(repo) == before
    else:
        original = promotion.validate_project
        def fail(root, **kwargs):
            if root == repo:
                if fault == "interrupt": raise KeyboardInterrupt()
                raise ValueError("final validation failed")
            return original(root, **kwargs)
        monkeypatch.setattr(promotion, "validate_project", fail)
        with pytest.raises((ValueError, KeyboardInterrupt)):
            promotion.promote(repo, source, confirm=lambda report: True)
        assert protected(repo) == before


def test_new_version_engineering_uses_manifest_and_archives_every_user_file(repo):
    # The synthetic candidate has a different chapter set and unit count from 1007.
    source = snapshot(repo)
    old = repo / "outputs/omegat-ipg-full"
    (old / "omegat").mkdir()
    (old / "omegat/full-review.mapping.json").write_text(json.dumps({"releaseId": load_project(repo)["manifest"]["releaseId"]}))
    (old / "omegat/project_save.tmx").write_bytes(b"USER TMX")
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    promotion.promote(repo, source, confirm=lambda report: True)
    glossary = repo / "terminology/ipg-glossary.txt"
    glossary.parent.mkdir(exist_ok=True)
    glossary.write_text("", encoding="utf-8")
    with project_context(repo):
        assert maintenance.prepare_new_version(lambda prompt: "n")["cancelled"]
        assert {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()} == before
        result = maintenance.prepare_new_version(lambda prompt: "y")
        archive = Path(result["archivedProject"])
        assert {p.relative_to(archive): p.read_bytes() for p in archive.rglob("*") if p.is_file()} == before
        mapping = load_json(full_review.MAPPING_PATH)
        assert len(mapping["units"]) != 1007
        assert set(mapping["poOrder"]) == {"display-values.po", "front-matter.po"}
        assert not list((old / "target").iterdir())
        assert not full_review.prepare_full_project()["created"]


def test_new_engineering_failure_restores_old_directory(repo, monkeypatch):
    old = repo / "outputs/omegat-ipg-full"
    (old / "omegat").mkdir()
    (old / "omegat/full-review.mapping.json").write_text('{"releaseId":"old-version"}')
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    def fail():
        (old / "source").mkdir(parents=True)
        (old / "source/partial.po").write_text("PARTIAL")
        raise KeyboardInterrupt()
    monkeypatch.setattr(maintenance, "prepare_or_continue", fail)
    with project_context(repo), pytest.raises(KeyboardInterrupt):
        maintenance.prepare_new_version(lambda prompt: "y")
    assert {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()} == before


def test_ambiguous_identity_blocks_promotion(repo):
    def change(parsed):
        parsed["document"]["sections"][0]["components"][0]["groups"][0]["blocks"][0]["text"]["en"] = "TEST ONLY changed authoritative English"
    report = _check(repo, payload=PDF + b"AMBI GUOUS", mutate=change)
    before = protected(repo)
    with pytest.raises(ValueError, match="未决问题"):
        promotion.promote(repo, repo / report["snapshot"], confirm=lambda report: True)
    assert protected(repo) == before


def test_second_update_keeps_lineage_without_recursively_embedding_archives(repo):
    first = promotion.promote(repo, snapshot(repo), confirm=lambda report: True)
    report = _check(repo, payload=PDF + b"SECOND UPDATE")
    source = repo / report["snapshot"]
    evidence = promotion.verify_snapshot(source)
    assert not any(name.startswith("snapshots/updates/") for name in evidence["context"]["baseFiles"])
    second = promotion.promote(repo, source, confirm=lambda report: True)
    assert second["baseReleaseId"] == first["releaseId"]
    assert validate_project(repo, profile="candidate")["valid"]
    assert not validate_project(repo, profile="release")["valid"]
