import json

import pytest

from ipg_pipeline import official_update as update, scheduled_update as scheduled
from ipg_pipeline.snapshot import main as snapshot_main
from tests.test_official_update import repo, _check, PDF


def test_failed_html_is_retained_before_retry(monkeypatch, tmp_path):
    payloads = iter([b"<html>temporarily empty</html>", b"<html>still empty</html>", b"<html>unavailable</html>"])
    monkeypatch.setattr(update, "download", lambda *args: (next(payloads), update.RULES_PAGE, []))
    sleeps = []
    monkeypatch.setattr(update.time, "sleep", sleeps.append)
    with pytest.raises(ValueError, match="IPG"):
        update._retry_official_request(
            lambda attempt, evidence: update.discover(diagnostics_dir=tmp_path, attempt=attempt),
            stage="rules-page", diagnostics_dir=tmp_path)
    assert sleeps == [5, 15]
    assert len(list(tmp_path.glob("*.html"))) == 3
    assert len(list(tmp_path.glob("*.json"))) == 3
    assert all(json.loads(path.read_text())["status"] == "failure" for path in tmp_path.glob("*.json"))


def test_transient_failure_recovers_without_losing_evidence(monkeypatch, tmp_path):
    attempts = []
    monkeypatch.setattr(update.time, "sleep", lambda seconds: None)
    def request(attempt, evidence):
        attempts.append(attempt)
        if attempt == 1:
            raise OSError("temporary network failure")
        return "success"
    assert update._retry_official_request(request, stage="official-pdf", diagnostics_dir=tmp_path) == "success"
    assert attempts == [1, 2]
    assert "temporary network failure" in (tmp_path / "official-pdf-attempt-1.json").read_text()


def test_pr_evidence_copy_preserves_snapshot_and_current_version(repo, monkeypatch):
    report = _check(repo, payload=PDF + b"new synthetic PDF")
    monkeypatch.setattr(scheduled, "ROOT", repo)
    before = {p.relative_to(repo).as_posix(): p.read_bytes()
              for folder in ("src", "review") for p in (repo / folder).rglob("*") if p.is_file()}
    destination = scheduled.stage_review_snapshot(report)
    original = update.verify_snapshot(repo / report["snapshot"])
    assert update.verify_snapshot(destination) == original
    assert destination.is_relative_to(repo / "snapshots/updates")
    assert scheduled.stage_review_snapshot(report) == destination
    assert snapshot_main(["--project-root", str(repo), "--verify", str(destination)]) == 0
    assert all((repo / name).read_bytes() == content for name, content in before.items())
    (destination / "official/IPG_EN.pdf").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        scheduled.stage_review_snapshot(report)
