"""The MTR-style CI must validate without becoming a second release writer."""

import yaml

from ipg_pipeline.core import ROOT


def _workflow():
    # BaseLoader preserves GitHub's YAML key `on`, unlike YAML 1.1 boolean loading.
    return yaml.load((ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8"),
                     Loader=yaml.BaseLoader)


def test_ci_retains_read_only_mtr_quality_gates_and_native_ipg_entrypoints():
    workflow = _workflow()
    assert set(workflow["on"]) == {"push", "pull_request", "merge_group", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "true"
    job = workflow["jobs"]["validate"]
    assert not job.get("continue-on-error")
    steps = job["steps"]
    names = [step["name"] for step in steps]
    gates = ["Resolve current release", "Strictly validate source", "Run tests",
             "Build release rehearsal", "Independently validate published JSON",
             "Rebuild for determinism check", "Verify deterministic output",
             "Verify checked-in artifacts are current"]
    assert [names.index(name) for name in gates] == sorted(names.index(name) for name in gates)
    assert all(not step.get("continue-on-error") for step in steps)
    runs = "\n".join(step.get("run", "") for step in steps)
    assert "current_release_dir" in runs and "ipg-2024-09-23" not in runs
    assert "python scripts/validate.py --profile release" in runs
    assert "python -m pytest" in runs
    assert "python scripts/validate_output.py --input outputs/ci-artifacts/rules.json" in runs
    assert "ipg-pilot" not in runs
    builds = [step["run"] for step in steps if "scripts/build.py" in step.get("run", "")]
    assert len(builds) == 2
    assert all("--profile release --rehearsal" in run and "--output outputs/ci-" in run for run in builds)
    assert "git push" not in runs and "gh release" not in runs and "secrets." not in str(workflow)
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] == "false"


def test_ci_checks_both_final_files_and_retains_isolated_evidence():
    steps = {step["name"]: step for step in _workflow()["jobs"]["validate"]["steps"]}
    for filename in ("rules.json", "IPG.md"):
        assert f"cmp outputs/ci-artifacts/{filename} outputs/ci-repeat/{filename}" in steps["Verify deterministic output"]["run"]
        assert f"cmp outputs/ci-artifacts/{filename} dist/{filename} ||" in steps["Verify checked-in artifacts are current"]["run"]
    assert steps["Verify checked-in artifacts are current"]["run"].count("exit 1") == 2
    assert "sha256sum --check SHA256SUMS" in steps["Verify artifact checksums"]["run"]
    for name, path in [("Upload preview artifacts", "outputs/ci-artifacts/"),
                       ("Upload test results", "outputs/ci-tests.xml")]:
        step = steps[name]
        assert "always()" in step["if"] and "hashFiles(" in step["if"]
        assert step["with"]["path"] == path and step["with"]["retention-days"] == "14"
        assert step["with"]["if-no-files-found"] == "error"
