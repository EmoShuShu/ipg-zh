import json
from types import SimpleNamespace

import pytest
import yaml

from ipg_pipeline import scheduled_update as scheduled
from ipg_pipeline.core import ROOT


@pytest.fixture
def report():
    return {"changed": True, "status": "same-day-repack", "baseReleaseId": "test-base",
            "snapshot": "outputs/official-update/test-snapshot",
            "official": {"effectiveDate": "2000-01-01", "pdfSha256": "a" * 64,
                         "url": "https://media.wizards.com/test.pdf"}}


def test_unchanged_does_not_contact_github(monkeypatch, tmp_path, report):
    report["changed"] = False
    def unexpected(*args, **kwargs):
        pytest.fail("Unchanged PDF must not create a notification")
    monkeypatch.setattr(scheduled.subprocess, "run", unexpected)
    assert scheduled.notify_update(report, tmp_path / "unused", "owner/repo") is None


@pytest.mark.parametrize("existing", [True, False])
def test_changed_pdf_notifies_once_including_closed_prs(monkeypatch, tmp_path, report, existing):
    body = scheduled.write_report(report, tmp_path, "https://github.com/owner/repo/actions/runs/1")
    calls = []
    monkeypatch.setattr(scheduled, "ROOT", tmp_path)
    monkeypatch.setattr(scheduled, "stage_review_snapshot", lambda report: tmp_path / "snapshots/updates/test")
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "ls-remote"]:
            return SimpleNamespace(returncode=2, stdout="", args=args)
        assert kwargs["check"] is True
        if args[0] == "git":
            return SimpleNamespace(returncode=0, stdout="")
        if args[2] == "list":
            assert args[args.index("--state") + 1] == "all"
            issues = [{"state": "CLOSED", "number": 1, "body": body.read_text(), "url": "https://github.com/owner/repo/pull/1"}] if existing else []
            return SimpleNamespace(stdout=json.dumps(issues))
        assert args[2] == "create"
        assert args[args.index("--body-file") + 1] == str(body)
        assert args[args.index("--assignee") + 1] == "owner"
        return SimpleNamespace(stdout="https://github.com/owner/repo/pull/2\n")
    monkeypatch.setattr(scheduled.subprocess, "run", run)
    result = scheduled.notify_update(report, body, "owner/repo")
    assert result.endswith("/1" if existing else "/2")
    assert len(calls) == (1 if existing else 9)
    if not existing:
        assert "--draft" in calls[-1]
        assert calls[-2][:2] == ["git", "push"]
        assert not any("src/ipg" in arg for args in calls for arg in args)
    assert json.loads((tmp_path / "result.json").read_text()) == report
    assert report["official"]["pdfSha256"] in body.read_text()


def test_pr_listing_failure_does_not_create_duplicates(monkeypatch, tmp_path, report):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        raise scheduled.subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(scheduled.subprocess, "run", run)
    with pytest.raises(scheduled.subprocess.CalledProcessError):
        scheduled.notify_update(report, tmp_path / "unused", "owner/repo")
    assert len(calls) == 1 and calls[0][2] == "list"


def test_scheduled_entrypoint_writes_summary_and_preserves_report_on_notification_failure(
    monkeypatch, tmp_path, report
):
    monkeypatch.setattr(scheduled, "ROOT", tmp_path)
    monkeypatch.setattr(scheduled, "check_official_update", lambda root, **kwargs: report)
    for name, value in {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_SERVER_URL": "https://github.com",
                        "GITHUB_RUN_ID": "123", "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}.items():
        monkeypatch.setenv(name, value)
    def failure(*args):
        raise scheduled.subprocess.CalledProcessError(1, "gh")
    monkeypatch.setattr(scheduled, "notify_update", failure)
    with pytest.raises(scheduled.subprocess.CalledProcessError):
        scheduled.main()
    assert json.loads((tmp_path / "outputs/official-update/result.json").read_text()) == report
    assert "https://github.com/owner/repo/actions/runs/123" in (tmp_path / "summary.md").read_text()


def test_daily_workflow_preserves_current_release_and_failure_evidence():
    workflow = yaml.load((ROOT / ".github/workflows/official-update.yml").read_text(),
                         Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "0 6 * * *", "timezone": "Asia/Shanghai"}]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["check"]["steps"]
    check = next(step for step in steps if "scheduled_update" in step.get("run", ""))
    assert "pipefail" in check["run"] and not check.get("continue-on-error")
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    assert "outputs/official-update/" in upload["with"]["path"]
    assert "tmp/official-update-diagnostics/" in upload["with"]["path"]
    assert upload["with"]["retention-days"] == "30"
