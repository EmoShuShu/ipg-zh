"""MTR-style daily operations preserve IPG's native identities and safety gates."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ipg_pipeline import full_review, review_assistant as assistant
from ipg_pipeline.core import ROOT
from ipg_pipeline.omegat import parse_po
from tests.test_full_review_workflow import isolated_repo, _create_target, _write_po


def _ready_targets(project):
    full_review.prepare_full_project()
    full_review.initialize_full_review_state()
    _create_target(project)
    for filename in full_review.PO_ORDER:
        entries = parse_po(project / "target" / filename)
        for entry in entries:
            if not entry["target"]:
                entry["target"] = "仅用于流程测试的合成译文"
        _write_po(project / "target" / filename, entries)


def _bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_complete_ready_batch_generates_final_files_in_one_action(isolated_repo):
    root, project = isolated_repo
    _ready_targets(project)
    result = assistant.complete_review_batch(full_review.PO_ORDER, lambda preview: True,
                                            finalize=True, test_runner=lambda: {"passed": True})
    assert result["final"]["kind"] == "formal-dist"
    assert result["progress"]["counts"]["unreviewed"] == 0
    assert result["progress"]["counts"]["stale"] == 0
    assert {p.name for p in (root / "dist").iterdir()} == {"IPG.md", "rules.json"}
    assert json.loads((root / "dist/rules.json").read_text())["candidate"] is False
    assert not (root / "outputs/current-candidate").exists()


def test_partial_review_keeps_progress_and_previous_dist(isolated_repo):
    root, project = isolated_repo
    full_review.prepare_full_project()
    full_review.initialize_full_review_state()
    _create_target(project)
    dist = root / "dist"
    dist.mkdir()
    (dist / "IPG.md").write_bytes(b"previous final version")
    before = _bytes(dist)
    result = assistant.complete_review_batch(["chapter-01.po"], lambda preview: True,
                                            finalize=True, test_runner=lambda: {"passed": True})
    assert result["final"] is None
    assert result["progress"]["counts"]["reviewed-unchanged"] > 0
    assert result["progress"]["counts"]["unreviewed"] > 0
    assert (root / "outputs/current-candidate/IPG.md").is_file()
    assert _bytes(dist) == before


def test_final_build_failure_restores_source_review_and_old_outputs(isolated_repo, monkeypatch):
    root, project = isolated_repo
    _ready_targets(project)
    folders = [root / name for name in ("src", "review", "dist", "outputs/current-candidate")]
    for path in folders[2:]:
        path.mkdir(parents=True)
        (path / "IPG.md").write_bytes(b"old reader output")
    before = {path: _bytes(path) for path in folders}
    mapping = full_review.MAPPING_PATH.read_bytes()
    def fail(*args, **kwargs):
        assert full_review.validate_current_state()["release"]["valid"]
        raise ValueError("injected final build failure")
    monkeypatch.setattr(assistant, "build_project", fail)
    with pytest.raises(ValueError, match="injected final"):
        assistant.complete_review_batch(full_review.PO_ORDER, lambda preview: True,
                                        finalize=True, test_runner=lambda: {"passed": True})
    assert {path: _bytes(path) for path in folders} == before
    assert full_review.MAPPING_PATH.read_bytes() == mapping


@pytest.mark.parametrize("answer,expected", [("a", ["1.po", "2.po"]), ("all", ["1.po", "2.po"]),
                                            ("q", []), ("c", []), ("2，1,2", ["2.po", "1.po"])])
def test_mtr_selection_conventions_keep_old_shortcuts(answer, expected):
    assert assistant.parse_file_selection(answer, ["1.po", "2.po"]) == expected


def test_invalid_selection_reprompts_and_enter_uses_modified_files(monkeypatch):
    answers = iter(["bad", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(assistant, "inspect_target_project", lambda: {"modifiedPo": ["chapter-01.po"]})
    assert assistant._choose_po() == ["chapter-01.po"]


def test_primary_finish_option_requests_final_output(monkeypatch, capsys):
    answers = iter(["2", "a"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(assistant, "inspect_target_project", lambda: {"modifiedPo": []})
    calls = []
    def complete(selected, confirm, **kwargs):
        calls.append((selected, kwargs["finalize"]))
        return {"cancelled": False, "final": {"kind": "formal-dist"}}
    monkeypatch.setattr(assistant, "complete_review_batch", complete)
    assert assistant.run_menu() == 0
    assert calls == [(full_review.PO_ORDER, True)]
    assert "dist/IPG.md" in capsys.readouterr().out


@pytest.mark.parametrize("answer,expected", [("y", True), ("yes", True), ("", False), ("n", False)])
def test_confirmation_matches_mtr_and_defaults_to_cancel(monkeypatch, answer, expected):
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    preview = {"selectedPo": ["chapter-01.po"], "reviewedUnitCount": 1,
               "actualChangeCount": 0, "reviewedUnchangedCount": 1, "noteUnitCount": 0}
    assert assistant._confirm(preview) is expected


@pytest.mark.parametrize("script", ["review_workflow.py", "export_omegat.py", "review_status.py",
                                    "audit_terminology.py", "create_snapshot.py"])
def test_common_script_names_work_outside_checkout(script, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
