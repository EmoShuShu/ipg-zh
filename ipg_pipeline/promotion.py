"""Inspect and explicitly adopt immutable official evidence as a review baseline.

Promotion is not publication or human review. Old releases, review stores and
OmegaT projects remain intact, and dist is never written here.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import tempfile

from .core import ROOT, load_json, load_yaml, sha256_bytes
from .production import _unchanged, load_project, validate_project
from .snapshot import json_bytes, verify_snapshot
from .transaction import restore_on_failure


def contained(root, relative):
    path = root / relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("候选提升路径不能经过链接")
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("候选提升路径越界")
    return path


def _plan(root: Path, snapshot: Path):
    root = root.resolve()
    snapshot = contained(root, snapshot)
    evidence = verify_snapshot(snapshot)
    report = load_json(snapshot / "reports/update.json")
    if not report["changed"]:
        raise ValueError("无更新的快照不能提升")
    # Context changes are deliberately stale review units. Lost/ambiguous anchors
    # and identities must be resolved in a new checker snapshot first.
    blockers = [f for f in report["findings"] if f["code"] != "publication-annotation-context-changed"]
    if blockers or load_json(snapshot / "reports/unresolved-annotations.json"):
        raise ValueError("候选仍有身份、注解锚点或批注未决问题，请重新生成候选：" + json.dumps(blockers, ensure_ascii=False))
    project = load_project(root)
    if project["manifest"]["releaseId"] != report["baseReleaseId"]:
        raise ValueError("当前版本已变化，不能套用旧候选")
    # Code may have been upgraded after the candidate was archived. Source,
    # schemas, ledgers and authority inputs must still be exactly the same.
    base = {str(contained(root, name)): digest for name, digest in evidence["context"]["baseFiles"].items()
            if not name.startswith("ipg_pipeline/")}
    _unchanged(base)
    manifest = load_yaml(snapshot / "candidate/source/manifest.yaml")
    identity = manifest["releaseId"]
    revision = manifest["versions"]["translation"]["revision"]
    if not re.fullmatch(r"ipg-[A-Za-z0-9_-]+", identity) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", revision):
        raise ValueError("候选版本身份不能用作安全路径")
    if manifest["versions"] != evidence["versions"]:
        raise ValueError("候选 manifest 与快照版本不一致")
    official = manifest["versions"]["official"]
    archive = Path("snapshots/updates") / official["effectiveDate"] / official["pdfSha256"] / evidence["contextHash"]
    writes = {}
    for file in snapshot.rglob("*"):
        if file.is_file():
            writes[(archive / file.relative_to(snapshot)).as_posix()] = file.read_bytes()
    for name in ["manifest.yaml", *manifest["documents"]]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.yaml", name):
            raise ValueError("候选源文件名不合法")
        writes[f"src/ipg/releases/{identity}/{name}"] = (snapshot / "candidate/source" / name).read_bytes()
    for store in ("status", "translation-notes"):
        name = f"review/{store}/{revision}.json"
        writes[name] = (snapshot / "candidate" / name).read_bytes()
    writes["src/ipg/id-registry.yaml"] = (snapshot / "proposed/id-registry.yaml").read_bytes()
    writes["src/ipg/mapping-overrides.yaml"] = (snapshot / "proposed/mapping-overrides.yaml").read_bytes()
    writes[f"snapshots/official/{official['effectiveDate']}/{official['pdfSha256']}/IPG_EN.pdf"] = (snapshot / "official/IPG_EN.pdf").read_bytes()
    prior = project["migration"]
    prior_evidence = load_json(contained(root, prior["evidence"]["path"]))
    authority = {"officialPdfSha256": official["pdfSha256"],
                 "legacySourceSha256": manifest["versions"]["annotations"]["sourceSha256"]}
    lineage = {"kind": "official-update-lineage", "authority": authority,
        "rawUnits": prior_evidence["rawUnits"], "snapshot": archive.as_posix(),
        "baseMigration": prior, "baseReleaseId": report["baseReleaseId"],
        "meaning": "Historical legacy coverage carried through verified identity reconciliation; not proof of translation or human review."}
    lineage_path = f"review/migration-evidence/{identity}.json"
    writes[lineage_path] = json_bytes(lineage)
    migration = copy.deepcopy(prior)
    migration.update(releaseId=identity, authority=authority,
                     evidence={"path": lineage_path, "sha256": sha256_bytes(writes[lineage_path])})
    writes[f"review/migration/{identity}.json"] = json_bytes(migration)
    writes["src/ipg/current-release.txt"] = (identity + "\n").encode()
    mutable = {"src/ipg/id-registry.yaml", "src/ipg/mapping-overrides.yaml", "src/ipg/current-release.txt"}
    for name, data in writes.items():
        path = contained(root, name)
        if name not in mutable and path.exists() and path.read_bytes() != data:
            raise ValueError("已有版本证据内容不同，拒绝覆盖：" + name)
    summary = {"baseReleaseId": report["baseReleaseId"], "releaseId": identity,
        "snapshot": archive.as_posix(), "diff": report["diff"]["summary"],
        "inheritance": report["inheritance"], "reviewWarnings": report["findings"],
        "distUnchanged": True, "omegatUnchanged": True}
    return summary, writes, base


def validate_lineage(root, evidence, manifest):
    """Validate the chain without relabeling the historical migration as new work."""
    if evidence.get("kind") != "official-update-lineage":
        return []
    snapshot = contained(root, evidence["snapshot"])
    record = verify_snapshot(snapshot)
    report = load_json(snapshot / "reports/update.json")
    prior = evidence["baseMigration"]
    prior_path = snapshot / "inputs/base/review/migration" / (evidence["baseReleaseId"] + ".json")
    prior_evidence_path = contained(snapshot / "inputs/base", prior["evidence"]["path"])
    prior_evidence = load_json(prior_evidence_path)
    if (load_json(prior_path) != prior or sha256_bytes(prior_evidence_path.read_bytes()) != prior["evidence"]["sha256"]
            or prior_evidence["authority"] != prior["authority"] or evidence["rawUnits"] != prior_evidence["rawUnits"]
            or record["versions"] != manifest["versions"] or report["baseReleaseId"] != evidence["baseReleaseId"]
            or any(f["code"] != "publication-annotation-context-changed" for f in report["findings"])
            or load_json(snapshot / "reports/unresolved-annotations.json")
            or load_yaml(snapshot / "candidate/source/manifest.yaml")["releaseId"] != manifest["releaseId"]):
        raise ValueError("官方更新继承证据链不一致")
    return [p for p in snapshot.rglob("*") if p.is_file()]


def _validate_plan(root, writes):
    with tempfile.TemporaryDirectory(prefix="ipg-promotion-check-") as name:
        staged = Path(name).resolve()
        for directory in ("src", "schema", "review", "snapshots", "docs"):
            if (root / directory).exists():
                shutil.copytree(root / directory, staged / directory)
        for name, data in writes.items():
            path = contained(staged, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        validation = validate_project(staged, profile="candidate")
        if not validation["valid"]:
            raise ValueError("候选提升结构验证失败：" + json.dumps(validation, ensure_ascii=False))
        return validation


def promote(root: Path, snapshot: Path, *, confirm=None):
    root = root.resolve()
    summary, writes, hashes = _plan(root, snapshot)
    summary["validation"] = _validate_plan(root, writes)
    if confirm is None:
        return {**summary, "status": "preview"}
    if not confirm(summary):
        return {**summary, "status": "cancelled"}
    # Approval binds to exactly this preview, not a later edited snapshot.
    fresh, fresh_writes, _ = _plan(root, snapshot)
    if fresh_writes != writes:
        raise ValueError("确认期间候选发生变化，未提升")
    _unchanged(hashes)
    pointer = "src/ipg/current-release.txt"
    paths = [contained(root, name) for name in writes]
    created_dirs = {parent for path in paths for parent in path.parents if parent.is_relative_to(root) and not parent.exists()}
    try:
        with restore_on_failure(paths):
            for name in [n for n in writes if n != pointer] + [pointer]:
                path = contained(root, name)
                if path.exists() and path.read_bytes() == writes[name]:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(writes[name])
            if not validate_project(root, profile="candidate")["valid"]:
                raise ValueError("提升后结构验证失败")
    except BaseException:
        for path in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        raise
    return {**summary, "status": "promoted-for-review"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="检查官方 IPG 候选；显式确认后仅切换到新审校基线")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="显示预览并请求 [y/N] 确认")
    args = parser.parse_args(argv)
    def confirm(summary):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return input("确认切换到此候选进行审校？[y/N]：").strip().casefold() in {"y", "yes"}
    try:
        result = promote(args.project_root, args.snapshot, confirm=confirm if args.apply else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "promoted-for-review":
            print("下一步：python scripts/export_omegat.py --project-root <本仓库路径> --new-version；旧工程会完整归档。")
        return 2 if result["status"] == "cancelled" else 0
    except KeyboardInterrupt:
        print("\n已取消；未完成的切换已恢复。")
        return 130
    except (ValueError, OSError, KeyError) as error:
        print(f"候选提升已停止：{error}")
        return 1
