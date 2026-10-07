"""Batch failures preserve reader output and user work; fixtures are synthetic."""
from pathlib import Path

import pytest

from ipg_pipeline import full_review, review_assistant
from ipg_pipeline.core import load_json, load_yaml, walk_nodes
from ipg_pipeline.omegat import parse_po
from test_full_review_workflow import isolated_repo, _create_target, _write_po


def _files(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("existing_candidate", [False, True])
@pytest.mark.parametrize("failure", ["tests", "interrupt", "build", "final-validation", "publish"])
def test_batch_failure_preserves_old_candidate_and_all_review_state(
    isolated_repo, monkeypatch, existing_candidate, failure
):
    root, project = isolated_repo
    full_review.prepare_full_project()
    full_review.initialize_full_review_state()
    full_review.refresh_progress_reports()
    full_review.run_nonblocking_terminology_audit()
    _create_target(project)
    entries = parse_po(project / "target/appendix-b.po")
    next(e for e in entries if not e["target"])["target"] = "仅用于事务测试的合成译文"
    _write_po(project / "target/appendix-b.po", entries)
    candidate = root / "outputs/current-candidate"
    if existing_candidate:
        candidate.mkdir()
        (candidate / "IPG.md").write_bytes(b"previous reader document\r\n")
        (candidate / "rules.json").write_bytes(b"previous rules\n")
        (candidate / "evidence").mkdir()
        (candidate / "evidence/user.txt").write_bytes(b"keep this too")
    directories = [root / "src", root / "review", project, candidate]
    before = {path: _files(path) for path in directories}
    reports = [full_review.PROGRESS_JSON, full_review.PROGRESS_MARKDOWN,
               full_review.TERMINOLOGY_JSON, full_review.TERMINOLOGY_MARKDOWN]
    reports_before = {path: path.read_bytes() for path in reports}

    def fail_tests():
        assert _files(root / "src") != before[root / "src"]
        if failure == "interrupt":
            raise KeyboardInterrupt("injected interrupt")
        raise ValueError("injected test gate failure")

    if failure == "build":
        original = review_assistant.build_current_candidate
        def fail_build(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError("injected build failure")
        monkeypatch.setattr(review_assistant, "build_current_candidate", fail_build)
    elif failure == "final-validation":
        original = review_assistant.validate_current_state
        calls = 0
        def fail_final():
            nonlocal calls
            calls += 1
            if calls == 2:
                # Failure after the new ledger and notes have been saved.
                assert _files(root / "review") != before[root / "review"]
                raise ValueError("injected final validation failure")
            return original()
        monkeypatch.setattr(review_assistant, "validate_current_state", fail_final)
    elif failure == "publish":
        original = Path.rename
        def fail_publish(path, target):
            if path.name == "reading" and target == candidate:
                raise OSError("injected directory publication failure")
            return original(path, target)
        monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises((ValueError, OSError, KeyboardInterrupt), match="injected"):
        review_assistant.complete_review_batch(
            ["appendix-b.po"], lambda preview: True,
            test_runner=fail_tests if failure in {"tests", "interrupt"} else lambda: {"passed": True},
        )
    assert {path: _files(path) for path in directories} == before
    assert {path: path.read_bytes() for path in reports} == reports_before
    assert candidate.exists() == existing_candidate
    assert not candidate.with_name(".current-candidate-previous").exists()
    assert not (root / "dist").exists()


def test_revised_body_annotation_and_filled_missing_targets_can_complete_batch(isolated_repo):
    root, project = isolated_repo
    full_review.prepare_full_project()
    full_review.initialize_full_review_state()
    _create_target(project)
    for name in full_review.PO_ORDER:
        entries = parse_po(project / "target" / name)
        for entry in entries:
            if not entry["target"]:
                entry["target"] = "仅用于缺译补齐回归测试，不是真实译文"
        if name == "chapter-02.po":
            next(e for e in entries if e["id"].startswith("annotation-block:"))["target"] += "（合成修改）"
        if name == "chapter-04.po":
            next(e for e in entries if e["id"].startswith("reading-segment:"))["target"] += "（合成修改）"
        _write_po(project / "target" / name, entries)
    result = review_assistant.complete_review_batch(
        full_review.PO_ORDER, lambda preview: True, test_runner=lambda: {"passed": True},
    )
    assert result["preview"]["actualChangeCount"] == 14
    assert len(result["preview"]["derivedChanges"]) == 1
    assert result["progress"]["initialMissingRemaining"] == 0
    assert result["progress"]["counts"] == {
        "unreviewed": 0, "stale": 0, "reviewed-modified": 14, "reviewed-unchanged": 993,
    }
    assert result["validation"]["candidate"]["valid"]
    release = result["validation"]["release"]
    assert not release["valid"]
    assert release["releaseGateCounts"]["missingTranslation"] == 0
    assert "annotation-license-pending" in release["readinessFindingCounts"]
    assert "manifest-not-publishable" in release["readinessFindingCounts"]
    assert result["candidate"]["byteIdentical"]
    assert (root / "outputs/current-candidate/IPG.md").is_file()
    rules = load_json(root / "outputs/current-candidate/rules.json")
    assert rules["publicationAnnotations"]
    # Newly translated official content has PDF evidence, not invented legacy evidence.
    appendix = load_yaml(full_review.RELEASE_DIR / "appendix-b.yaml")
    new = [n for kind, n in walk_nodes([appendix]) if kind == "block" and not n.get("legacyRawUnits")]
    assert len(new) == 12 and all(n["text"]["zh"] and n["officialPdfUnits"] for n in new)
    assert not (root / "dist").exists()


def test_failed_directory_restore_keeps_recoverable_old_candidate(isolated_repo, monkeypatch):
    root, _ = isolated_repo
    destination = root / "outputs/current-candidate"
    staged = root / "outputs/staged"
    destination.mkdir(parents=True)
    staged.mkdir()
    (destination / "IPG.md").write_bytes(b"original reader output")
    original = Path.rename
    def fail_rename(path, target):
        if target == destination:
            raise OSError("injected publish and restore failure")
        return original(path, target)
    monkeypatch.setattr(Path, "rename", fail_rename)
    with pytest.raises(ValueError, match="人工恢复"):
        full_review._replace_candidate(staged, destination)
    backup = destination.with_name(".current-candidate-previous")
    assert (backup / "IPG.md").read_bytes() == b"original reader output"
    with pytest.raises(ValueError, match="不会覆盖"):
        full_review._replace_candidate(staged, destination)
