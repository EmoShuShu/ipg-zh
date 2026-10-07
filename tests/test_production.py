from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ipg_pipeline import production, review_assistant, full_review
from ipg_pipeline.core import ROOT, dump_yaml, load_json, load_yaml, sha256_bytes, sha256_text, walk_nodes
from ipg_pipeline.omegat import collect_units
from ipg_pipeline.release import current_release_dir


TEST_RELEASE = "ipg-test-only__ann-fixture__zh-test"


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(value), encoding="utf-8")


def make_rehearsal(root):
    """Entirely synthetic: no authentic untranslated Chinese or real ledger is edited."""
    shutil.copytree(ROOT / "schema", root / "schema")
    release = root / "src/ipg/releases" / TEST_RELEASE
    release.mkdir(parents=True)
    (root / "src/ipg/current-release.txt").write_text(TEST_RELEASE + "\n", encoding="utf-8")
    (root / "src/ipg/version-notes.md").write_text("# 版本说明\n\n仅供合成发布演练，不是真实 IPG。\n", encoding="utf-8")
    pdf = b"TEST ONLY - synthetic snapshot - not the official IPG PDF"
    legacy = b"TEST ONLY - synthetic legacy snapshot"
    pdf_sha, legacy_sha = sha256_bytes(pdf), sha256_bytes(legacy)
    pdf_path = root / f"snapshots/official/2000-01-01/{pdf_sha}/test-only.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(pdf)
    legacy_path = root / f"snapshots/legacy/{legacy_sha}/AIPG_2025.md"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(legacy)
    document = load_yaml(ROOT / "tests/fixtures/release-rehearsal/source.yaml")
    document["sections"][0]["components"][0]["groups"][0]["blocks"][0]["officialPdfUnits"][0]["pdfSha256"] = pdf_sha
    _yaml(release / "test-only.yaml", document)
    manifest = {"schemaVersion": 1, "releaseId": TEST_RELEASE, "publishable": True,
                "scope": {"officialContent": {"mode": "full-document", "included": ["synthetic-only"]},
                          "publicationAnnotations": {"mode": "full-document", "includedSections": ["synthetic-only"], "deferredGroups": 0, "deferredRawUnits": 0}},
                "versions": {"official": {"effectiveDate": "2000-01-01", "pdfSha256": pdf_sha, "downloadedAt": "2000-01-01T00:00:00Z", "url": "https://example.invalid/synthetic-only"},
                             "annotations": {"version": "test-only", "sourceId": "synthetic-only", "sourceSha256": legacy_sha, "licenseStatus": "complete", "attribution": "synthetic test authors, not actual IPG attribution"},
                             "translation": {"locale": "zh-CN", "revision": "zh-test"}},
                "build": {"schema": "ipg-output-v1"}, "documents": ["test-only.yaml"]}
    _yaml(release / "manifest.yaml", manifest)
    display = {"schemaVersion": 1, "values": {"component.body": {"en": "Test body", "zh": "测试正文"}}}
    _yaml(root / "src/ipg/display-values.yaml", display)
    _yaml(root / "src/ipg/id-registry.yaml", {"entries": [{"id": node["id"], "kind": kind, "status": "active", "canonicalKey": f"test:{node['id']}", "history": []}
        for kind, node in walk_nodes([document])]})
    units = collect_units([(manifest["documents"][0], document)], display, release_relative=release.relative_to(root).as_posix())
    _json(root / "review/status/zh-test.json", {"schemaVersion": 1, "releaseId": TEST_RELEASE, "translationRevision": "zh-test", "entries": [
        {"unitId": u["id"], "sourceHash": sha256_text(u["source"]), "targetHash": sha256_text(u["target"]), "status": "reviewed-unchanged", "reviewedAt": "2000-01-01"} for u in units]})
    authority = {"officialPdfSha256": pdf_sha, "legacySourceSha256": legacy_sha}
    summary = {"authority": authority, "rawUnits": {"total": 1, "disposed": 1, "duplicateConsumption": 0, "unresolved": 0}}
    evidence_path = root / "tests/synthetic-migration-summary.json"
    _json(evidence_path, summary)
    _json(root / f"review/migration/{TEST_RELEASE}.json", {"schemaVersion": 1, "releaseId": TEST_RELEASE, "authority": authority,
        "evidence": {"path": evidence_path.relative_to(root).as_posix(), "sha256": sha256_bytes(evidence_path.read_bytes())},
        "coverage": {"rawUnitCount": 1, "disposedUnitCount": 1, "duplicateConsumption": 0, "unresolved": 0}, "findings": []})
    assert production.validate_project(root, profile="release")["valid"]
    return root


@pytest.fixture
def rehearsal_repo(tmp_path):
    return make_rehearsal(tmp_path / "synthetic-rehearsal")


def _dist(root, existing):
    if existing:
        (root / "dist").mkdir()
        for filename in production.FINAL_FILES:
            (root / "dist" / filename).write_bytes(b"PREVIOUS " + filename.encode())
    return _dist_bytes(root)


def _dist_bytes(root):
    return {p.name: p.read_bytes() for p in (root / "dist").iterdir()} if (root / "dist").exists() else None


@pytest.mark.parametrize("existing", [False, True])
def test_release_success_updates_whole_dist_after_two_independent_checks(rehearsal_repo, monkeypatch, existing):
    root = rehearsal_repo
    _dist(root, existing)
    before = (root / "review/status/zh-test.json").read_bytes()
    checks = []
    real = production.validate_output
    def check(path, checked_root):
        checks.append(path)
        assert path.is_relative_to(root) and "dist" not in path.parts
        return real(path, checked_root)
    monkeypatch.setattr(production, "validate_output", check)
    assert production.validate_project(root, profile="release")["valid"]
    report = production.build_project(root, profile="release")
    assert len(checks) == 2 and report["byteIdentical"] and report["outputSchemaValid"]
    assert set(_dist_bytes(root)) == set(production.FINAL_FILES)
    assert real(root / "dist/rules.json", root)["valid"]
    rules = load_json(root / "dist/rules.json")
    assert not rules["candidate"] and rules["publishable"] and rules["releaseId"] == TEST_RELEASE
    assert "仅供合成发布演练" in rules["versionNotes"]["zh"]
    assert "仅供合成发布演练" in (root / "dist/IPG.md").read_text(encoding="utf-8")
    assert (root / "review/status/zh-test.json").read_bytes() == before
    assert report["hashes"]["rules.json"].encode() not in (root / "dist/rules.json").read_bytes()
    assert not list(root.glob(".ipg-build-*")) and not (root / ".ipg-dist.lock").exists()


@pytest.mark.parametrize("fault", ["translation", "ledger-missing", "record-missing", "unreviewed", "stale", "target-hash", "source-hash", "ledger-version", "ledger-shape", "orphan", "duplicate", "license", "attribution", "publishable", "annotation-scope", "registry", "migration-evidence", "pdf-hash", "source-schema"])
@pytest.mark.parametrize("existing", [False, True])
def test_release_failure_preserves_absent_or_existing_dist(rehearsal_repo, fault, existing):
    root = rehearsal_repo
    before = _dist(root, existing)
    release = current_release_dir(root)
    manifest_path = release / "manifest.yaml"
    ledger_path = root / "review/status/zh-test.json"
    manifest, ledger = load_yaml(manifest_path), load_json(ledger_path)
    if fault == "translation":
        source = load_yaml(release / "test-only.yaml")
        source["sections"][0]["title"]["zh"] = ""
        _yaml(release / "test-only.yaml", source)
    elif fault == "ledger-missing": ledger_path.unlink()
    elif fault in {"record-missing", "orphan", "duplicate", "ledger-version", "ledger-shape", "unreviewed", "stale", "target-hash", "source-hash"}:
        if fault == "record-missing": ledger["entries"].pop()
        elif fault == "orphan": ledger["entries"].append({**ledger["entries"][0], "unitId": "deleted:unit"})
        elif fault == "duplicate": ledger["entries"].append(copy.deepcopy(ledger["entries"][0]))
        elif fault == "ledger-version": ledger["releaseId"] = "wrong-version"
        elif fault == "ledger-shape": ledger["entries"][0]["status"] = "fake-reviewed"
        elif fault in {"unreviewed", "stale"}: ledger["entries"][0]["status"] = fault
        else: ledger["entries"][0]["targetHash" if fault == "target-hash" else "sourceHash"] = "0" * 64
        _json(ledger_path, ledger)
    elif fault in {"license", "attribution", "publishable", "annotation-scope"}:
        if fault == "license": manifest["versions"]["annotations"]["licenseStatus"] = "pending"
        elif fault == "attribution": manifest["versions"]["annotations"]["attribution"] = ""
        elif fault == "publishable": manifest["publishable"] = False
        else: manifest["scope"]["publicationAnnotations"]["mode"] = "pilot"
        _yaml(manifest_path, manifest)
    elif fault == "registry": _yaml(root / "src/ipg/id-registry.yaml", {"entries": []})
    elif fault == "migration-evidence": (root / "tests/synthetic-migration-summary.json").write_bytes(b"{}")
    elif fault == "pdf-hash": next((root / "snapshots/official").rglob("*.pdf")).write_bytes(b"changed")
    else:
        source = load_yaml(release / "test-only.yaml")
        del source["sections"][0]["components"][0]["groups"][0]["blocks"][0]["text"]
        _yaml(release / "test-only.yaml", source)
    with pytest.raises((ValueError, KeyError)):
        production.build_project(root, profile="release")
    assert _dist_bytes(root) == before
    assert not list(root.glob(".ipg-build-*"))


@pytest.mark.parametrize("fault", ["output-schema", "second-build", "input-race", "promotion"])
@pytest.mark.parametrize("existing", [False, True])
def test_late_failures_do_not_partially_publish(rehearsal_repo, monkeypatch, fault, existing):
    root = rehearsal_repo
    before = _dist(root, existing)
    real_build = production.build_outputs
    def build(path, *args, **kwargs):
        result = real_build(path, *args, **kwargs)
        if fault == "output-schema":
            rules = load_json(path / "rules.json"); del rules["sections"][0]["components"][0]["groups"][0]["blocks"][0]["text"]
            _json(path / "rules.json", rules)
        elif fault == "second-build" and path.name == "second":
            with (path / "IPG.md").open("a", encoding="utf-8") as stream: stream.write("\nNONDETERMINISTIC\n")
        elif fault == "input-race":
            (root / "src/ipg/version-notes.md").write_text("# 版本说明\n\n构建期间发生编辑\n", encoding="utf-8")
        return result
    monkeypatch.setattr(production, "build_outputs", build)
    if fault == "promotion":
        real_replace = production.os.replace
        def replace(source, destination):
            if destination == root / "dist": raise OSError("simulated directory publication failure")
            return real_replace(source, destination)
        monkeypatch.setattr(production.os, "replace", replace)
    expected = {"output-schema": "独立输出 schema", "second-build": "两次构建字节不一致",
                "input-race": "输入文件发生变化", "promotion": "simulated directory publication failure"}
    with pytest.raises((ValueError, OSError), match=expected[fault]):
        production.build_project(root, profile="release")
    assert _dist_bytes(root) == before
    assert not (root / ".ipg-dist.lock").exists()


@pytest.mark.parametrize("kind", ["candidate", "rehearsal"])
def test_nonformal_build_never_writes_dist_and_preserves_existing_outputs(rehearsal_repo, kind):
    root = rehearsal_repo
    before = _dist(root, True)
    kwargs = {"profile": "candidate"} if kind == "candidate" else {"profile": "release", "rehearsal": True}
    with pytest.raises(ValueError, match="outputs"):
        production.build_project(root, **kwargs, output=root / "dist")
    destination = root / "outputs" / kind
    report = production.build_project(root, **kwargs, output=destination)
    assert report["kind"] == ("release-rehearsal" if kind == "rehearsal" else "candidate")
    original = {p.name: p.read_bytes() for p in destination.iterdir()}
    with pytest.raises(ValueError, match="目录已存在"):
        production.build_project(root, **kwargs, output=destination)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == original
    assert _dist_bytes(root) == before


@pytest.mark.parametrize("problem", ["missing", "malformed", "findings"])
def test_terminology_remains_nonblocking_for_release(rehearsal_repo, problem):
    root = rehearsal_repo
    glossary = root / "terminology/ipg-glossary.txt"
    if problem != "missing":
        glossary.parent.mkdir()
        glossary.write_text("bad glossary row\n" if problem == "malformed" else "Synthetic\t绝不匹配\n", encoding="utf-8")
    report = production.build_project(root, profile="release")
    assert report["terminology"]["nonBlocking"]
    if problem == "missing": assert "error" in report["terminology"]
    elif problem == "malformed": assert report["terminology"]["glossaryIssueCount"]
    else: assert report["terminology"]["findingCount"]


@pytest.mark.parametrize("pointer", ["../escape", "D:\\elsewhere", "ipg-does-not-exist", "ipg-a\nipg-b"])
def test_pointer_is_fail_closed(rehearsal_repo, pointer):
    (rehearsal_repo / "src/ipg/current-release.txt").write_text(pointer, encoding="utf-8")
    with pytest.raises(ValueError): production.validate_project(rehearsal_repo, profile="release")
    assert not (rehearsal_repo / "dist").exists()


def test_pointer_switch_not_hardcoded_and_menu_release_entry(rehearsal_repo, monkeypatch, capsys):
    root = rehearsal_repo
    new_id = "ipg-test-next__ann-fixture__zh-test"
    old = current_release_dir(root)
    new = old.with_name(new_id)
    shutil.copytree(old, new)
    manifest = load_yaml(new / "manifest.yaml"); manifest["releaseId"] = new_id
    _yaml(new / "manifest.yaml", manifest)
    migration = load_json(root / f"review/migration/{TEST_RELEASE}.json"); migration["releaseId"] = new_id
    _json(root / f"review/migration/{new_id}.json", migration)
    ledger = load_json(root / "review/status/zh-test.json"); ledger["releaseId"] = new_id
    _json(root / "review/status/zh-test.json", ledger)
    (root / "src/ipg/current-release.txt").write_text(new_id, encoding="utf-8")
    monkeypatch.setattr(full_review, "ROOT", root)
    assert full_review.full_inputs()[0]["releaseId"] == new_id
    monkeypatch.setattr(review_assistant, "ROOT", root)
    assert review_assistant.check_formal_release()["valid"]
    assert review_assistant.finish_formal_release()["releaseId"] == new_id
    assert "已生成最终文件" in capsys.readouterr().out


def test_real_project_and_helper_are_blocked_without_changing_user_ledger(monkeypatch, capsys):
    ledger = ROOT / "review/status/zh-r0001.json"
    before = ledger.read_bytes()
    assert production.validate_project(ROOT, profile="candidate")["valid"]
    report = production.validate_project(ROOT, profile="release")
    project = production.load_project(ROOT)
    missing = sum(not unit["target"] for unit in collect_units(project["documents"], project["display"]))
    assert not report["valid"] and report["releaseGateCounts"]["missingTranslation"] == missing
    assert "annotation-license-pending" in report["readinessFindingCounts"]
    assert "manifest-not-publishable" in report["readinessFindingCounts"]
    assert review_assistant.finish_formal_release() is None
    output = capsys.readouterr().out
    assert "注解" in output and "publishable: false" in output
    with pytest.raises(ValueError): production.build_project(ROOT, profile="release")
    assert ledger.read_bytes() == before and not (ROOT / "dist").exists()


@pytest.mark.parametrize("entry", ["validate.py", "build.py", "validate_output.py"])
def test_scripts_are_usable_from_outside_repository(entry, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / entry), "--help"], cwd=tmp_path,
                            env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0 and "ipg-" in result.stdout


def test_cli_profiles_output_validation_and_release_rehearsal(rehearsal_repo, capsys):
    root = rehearsal_repo
    for function in (production.validate_main, production.build_main):
        with pytest.raises(SystemExit): function(["--project-root", str(root)])
    assert production.validate_main(["--project-root", str(root), "--profile", "release"]) == 0
    assert production.build_main(["--project-root", str(root), "--profile", "release", "--rehearsal"]) == 0
    assert not (root / "dist").exists()
    assert production.output_main(["--project-root", str(root), "--input", str(root / "outputs/p4-6/release-rehearsal/rules.json")]) == 0
    assert production.output_main(["--project-root", str(root)]) == 1
    assert "输出检查已停止" in capsys.readouterr().out


def test_publication_never_exposes_partly_updated_files(rehearsal_repo, monkeypatch):
    root = rehearsal_repo
    _dist(root, True)
    real = production.os.replace
    calls = []
    def replace(staged, destination):
        assert destination == root / "dist"
        assert not destination.exists()
        assert {p.name for p in staged.iterdir()} == set(production.FINAL_FILES)
        assert production.validate_output(staged / "rules.json", root)["valid"]
        calls.append(staged)
        return real(staged, destination)
    monkeypatch.setattr(production.os, "replace", replace)
    report = production.build_project(root, profile="release")
    assert len(calls) == 1 and report["publication"]["unit"] == "whole-directory"
    assert report["publication"]["briefAbsentPathWindow"]
    assert not list(root.glob(".ipg-build-*-previous-dist"))


def test_failed_restore_keeps_recoverable_previous_dist(rehearsal_repo, monkeypatch):
    root = rehearsal_repo
    before = _dist(root, True)
    real_rename = Path.rename
    def rename(path, destination):
        if path.name.endswith("-previous-dist"):
            raise OSError("simulated failed restore")
        return real_rename(path, destination)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(production.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("publication failed")))
    with pytest.raises(ValueError, match="旧文件完整保存在"):
        production.build_project(root, profile="release")
    backups = list(root.glob(".ipg-build-*-previous-dist"))
    assert len(backups) == 1
    assert {p.name: p.read_bytes() for p in backups[0].iterdir()} == before


def test_build_lock_refuses_a_second_writer_without_touching_dist(rehearsal_repo):
    root = rehearsal_repo
    before = _dist(root, True)
    (root / ".ipg-dist.lock").write_text("existing build lock", encoding="utf-8")
    with pytest.raises(FileExistsError): production.build_project(root, profile="release")
    assert _dist_bytes(root) == before
    assert (root / ".ipg-dist.lock").read_text(encoding="utf-8") == "existing build lock"


def test_candidate_rejects_outputs_redirected_into_dist(rehearsal_repo, monkeypatch):
    root = rehearsal_repo
    real_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        resolved = real_resolve(path, *args, **kwargs)
        if resolved.is_relative_to(root / "outputs"):
            return root / "dist" / resolved.relative_to(root / "outputs")
        return resolved
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="不能写入 dist"):
        production.candidate_destination(root, root / "outputs/candidate")


def test_output_command_resolves_current_manifest_without_requiring_review_ledger(rehearsal_repo, capsys):
    root = rehearsal_repo
    production.build_project(root, profile="release")
    (root / "review/status/zh-test.json").unlink()
    assert production.output_main(["--project-root", str(root)]) == 0
    output = root / "dist/rules.json"
    rules = load_json(output)
    rules["releaseId"] = "ipg-other-release"
    _json(output, rules)
    assert production.validate_output(output, root)["valid"]
    assert production.output_main(["--project-root", str(root)]) == 1
    assert "输出 releaseId 与当前 release 指针不一致" in capsys.readouterr().out
