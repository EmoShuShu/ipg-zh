from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .builder import VERSION_NOTES_PATH, build_outputs, read_version_notes
from .core import ROOT, load_json, load_yaml, sha256_bytes, validate_registry, validate_schema, walk_nodes
from .omegat import collect_units
from .release import current_release_dir
from .review import terminology_audit
from .validation import validate_release


FINAL_FILES = ("IPG.md", "rules.json")
BUILD_FILES = (*FINAL_FILES, "SHA256SUMS", "build-report.json")


def _contained(root: Path, relative: str | Path) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"路径越出本地项目：{relative}")
    return path


def _hashes(paths: list[Path]) -> dict[str, str | None]:
    return {str(path): sha256_bytes(path.read_bytes()) if path.is_file() else None for path in paths}


def _unchanged(hashes: dict[str, str | None]) -> None:
    if _hashes([Path(path) for path in hashes]) != hashes:
        raise ValueError("构建期间输入文件发生变化；已拒绝更新，请重新检查。")


def _current_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    release = current_release_dir(root)
    manifest_path = release / "manifest.yaml"
    manifest = load_yaml(manifest_path)
    errors = validate_schema(manifest, root / "schema/ipg-manifest.schema.json")
    if errors or manifest["releaseId"] != release.name:
        raise ValueError("manifest 不合法或与当前 release 指针不一致：" + "; ".join(errors))
    return release, manifest


def load_project(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    release, manifest = _current_manifest(root)
    manifest_path = release / "manifest.yaml"
    revision = manifest["versions"]["translation"]["revision"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", revision):
        raise ValueError("中文修订号不能作为安全的版本化审校路径。")
    names = manifest["documents"]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+\.yaml", name) for name in names):
        raise ValueError("manifest documents 必须是本 release 内的 YAML 文件名。")
    ledger_path = root / f"review/status/{revision}.json"
    migration_path = root / f"review/migration/{release.name}.json"
    registry_path = root / "src/ipg/id-registry.yaml"
    display_path = root / "src/ipg/display-values.yaml"
    migration = load_json(migration_path)
    errors = validate_schema(migration, root / "schema/migration-completion.schema.json")
    if errors:
        raise ValueError("迁移完成证据不合法：" + "; ".join(errors))
    authority = manifest["versions"]
    expected_authority = {"officialPdfSha256": authority["official"]["pdfSha256"],
                          "legacySourceSha256": authority["annotations"]["sourceSha256"]}
    if migration["releaseId"] != release.name or migration["authority"] != expected_authority:
        raise ValueError("迁移完成证据与当前 release/来源版本不一致。")
    evidence_path = _contained(root, migration["evidence"]["path"])
    evidence = load_json(evidence_path)
    raw = evidence["rawUnits"]
    expected_coverage = {"rawUnitCount": raw["total"], "disposedUnitCount": raw["disposed"],
                         "duplicateConsumption": raw["duplicateConsumption"], "unresolved": raw["unresolved"]}
    if (sha256_bytes(evidence_path.read_bytes()) != migration["evidence"]["sha256"]
            or evidence["authority"] != expected_authority or expected_coverage != migration["coverage"]):
        raise ValueError("迁移完成证据哈希、来源或覆盖计数不一致。")
    official = authority["official"]
    pdf_dir = _contained(root, f"snapshots/official/{official['effectiveDate']}/{official['pdfSha256']}")
    pdfs = list(pdf_dir.glob("*.pdf"))
    if len(pdfs) != 1 or sha256_bytes(pdfs[0].read_bytes()) != official["pdfSha256"]:
        raise ValueError("官方 PDF 快照缺失或 SHA-256 不一致。")
    legacy = _contained(root, f"snapshots/legacy/{authority['annotations']['sourceSha256']}/AIPG_2025.md")
    if sha256_bytes(legacy.read_bytes()) != authority["annotations"]["sourceSha256"]:
        raise ValueError("旧源快照 SHA-256 不一致。")
    paths = [root / "src/ipg/current-release.txt", manifest_path, display_path, registry_path,
             ledger_path, migration_path, evidence_path, pdfs[0], legacy, root / VERSION_NOTES_PATH,
             *(release / name for name in names), *sorted((root / "schema").glob("*.schema.json"))]
    hashes = _hashes(paths)
    # Re-read identity after capturing inputs, so a pointer/manifest race is fail-closed.
    if current_release_dir(root) != release or load_yaml(manifest_path) != manifest:
        raise ValueError("读取过程中当前版本身份发生变化。")
    if (load_json(migration_path) != migration or load_json(evidence_path) != evidence
            or hashes[str(pdfs[0])] != official["pdfSha256"]
            or hashes[str(legacy)] != authority["annotations"]["sourceSha256"]):
        raise ValueError("读取过程中迁移证据或权威快照发生变化。")
    documents = [(name, load_yaml(release / name)) for name in names]
    project = {"root": root, "release": release, "manifest": manifest, "documents": documents,
               "display": load_yaml(display_path), "registry": load_yaml(registry_path),
               "ledger": load_json(ledger_path) if ledger_path.exists() else None,
               "migration": migration, "hashes": hashes}
    read_version_notes(root)
    _unchanged(hashes)
    return project


def validate_project(root: Path = ROOT, *, profile: str) -> dict[str, Any]:
    return _validate_project(load_project(root), profile=profile)


def _validate_project(project: dict[str, Any], *, profile: str) -> dict[str, Any]:
    root, manifest = project["root"], project["manifest"]
    display_errors = validate_schema(project["display"], root / "schema/display-values.schema.json")
    if display_errors:
        raise ValueError("集中显示值不合法：" + "; ".join(display_errors))
    ledger_errors = []
    if project["ledger"] is not None:
        ledger_errors = validate_schema(project["ledger"], root / "schema/review-ledger.schema.json")
    migration = project["migration"]
    if migration["coverage"]["unresolved"]:
        migration = {**migration, "findings": [*migration["findings"],
            {"code": "unresolved-mapping", "count": migration["coverage"]["unresolved"]}]}
    report = validate_release(profile=profile, manifest=manifest,
        documents=[doc for _, doc in project["documents"]], display_values=project["display"],
        migration_report=migration, review_ledger=None if ledger_errors else project["ledger"], schema_dir=root / "schema")
    errors = validate_registry(project["registry"])
    if project["ledger"] is not None:
        # Malformed ledger is a release readiness problem, not a candidate blocker.
        if (project["ledger"].get("releaseId") != manifest["releaseId"]
                or project["ledger"].get("translationRevision") != manifest["versions"]["translation"]["revision"]):
            ledger_errors.append("审校账本的 releaseId / translationRevision 与当前版本不一致")
        report["readinessFindings"].extend({"code": "review-ledger-invalid", "detail": item} for item in ledger_errors)
    if not report["structuralFindings"]:
        active = {entry["id"] for entry in project["registry"]["entries"] if entry["status"] == "active"}
        nodes = [node for _, node in walk_nodes(doc for _, doc in project["documents"])]
        for _, doc in project["documents"]:
            for annotation in doc["publicationAnnotations"]:
                nodes.extend([annotation, *annotation["groups"], *(b for g in annotation["groups"] for b in g["blocks"])])
        nodes.extend(segment for node in list(nodes) for segment in node.get("readingSegments", []))
        errors.extend(f"节点未注册或已经退役：{node['id']}" for node in nodes if node["id"] not in active)
        for node in nodes:
            for provenance in node.get("officialPdfUnits", []):
                if provenance["pdfSha256"] != manifest["versions"]["official"]["pdfSha256"]:
                    errors.append(f"PDF 溯源版本不一致：{node['id']}")
    report["structuralFindings"].extend({"code": "production-source", "detail": item} for item in errors)
    for name in ("structural", "readiness"):
        report[f"{name}FindingCounts"] = dict(sorted(Counter(item["code"] for item in report[f"{name}Findings"]).items()))
    report["valid"] = not report["structuralFindings"] and (profile == "candidate" or not report["readinessFindings"])
    report["meaning"] = "publishable" if report["valid"] and profile == "release" else "reviewable-not-publishable" if report["valid"] else "validation-failed"
    report["releaseId"] = manifest["releaseId"]
    _unchanged(project["hashes"])
    return report


def validate_output(path: Path, root: Path = ROOT) -> dict[str, Any]:
    errors = validate_schema(load_json(path), root / "schema/ipg-output.schema.json")
    return {"valid": not errors, "errors": errors, "input": str(path)}


def candidate_destination(root: Path, output: Path) -> Path:
    path = output.resolve()
    base = (root / "outputs").resolve()
    if (not base.is_relative_to(root.resolve()) or not path.is_relative_to(base) or path == base
            or path.is_relative_to((root / "dist").resolve())):
        raise ValueError("candidate / rehearsal 只能写入本项目 outputs 下的独立目录，不能写入 dist。")
    return path


def _publish_directory(staged: Path, destination: Path, temporary: Path) -> dict[str, Any]:
    if list(destination.parent.glob(".ipg-build-*-previous-dist")):
        raise ValueError("检测到尚未处理的 dist 恢复备份；请先人工检查，不会覆盖。")
    if destination.is_symlink() or destination.is_junction() or (destination.exists() and
            (not destination.is_dir() or {p.name for p in destination.iterdir()} != set(FINAL_FILES)
             or any(not p.is_file() or p.is_symlink() for p in destination.iterdir()))):
        raise ValueError("dist 含非预期文件或链接；不会覆盖，请人工检查。")
    # A failed restore must survive TemporaryDirectory cleanup and process exit.
    backup = temporary.with_name(temporary.name + "-previous-dist")
    if destination.exists():
        destination.rename(backup)
    try:
        os.replace(staged, destination)
    except BaseException:
        if backup.exists():
            try:
                backup.rename(destination)
            except OSError as error:
                raise ValueError(f"dist 无法自动恢复；旧文件完整保存在 {backup}，请人工恢复。") from error
        raise
    publication = {"unit": "whole-directory", "rollbackOnError": True,
                   "kernelRenameExchange": False, "briefAbsentPathWindow": backup.exists()}
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError:
            publication["retainedBackup"] = str(backup)
    return publication


def build_project(root: Path = ROOT, *, profile: str, rehearsal: bool = False,
                  output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    if profile not in {"candidate", "release"} or (rehearsal and profile != "release"):
        raise ValueError("正式构建/演练必须明确使用 --profile release。")
    if profile == "release" and not rehearsal and output is not None:
        raise ValueError("正式构建固定写入 dist；自定义输出仅限 candidate / rehearsal。")
    destination = root / "dist" if profile == "release" and not rehearsal else candidate_destination(
        root, output or root / "outputs/p4-6" / ("release-rehearsal" if rehearsal else "candidate"))
    project = load_project(root)
    validation = _validate_project(project, profile=profile)
    if not validation["valid"]:
        raise ValueError("构建已拒绝：\n" + describe_validation(validation))
    with tempfile.TemporaryDirectory(prefix=".ipg-build-", dir=root, ignore_cleanup_errors=True) as name:
        temporary = Path(name)
        for label in ("first", "second"):
            build_outputs(temporary / label, project["manifest"], [doc for _, doc in project["documents"]],
                          project["display"], candidate=profile == "candidate", profile=profile, source_root=root)
            output_report = validate_output(temporary / label / "rules.json", root)
            if not output_report["valid"]:
                raise ValueError("独立输出 schema 验证失败：" + "; ".join(output_report["errors"]))
            _unchanged(project["hashes"])
        if any((temporary / "first" / file).read_bytes() != (temporary / "second" / file).read_bytes() for file in BUILD_FILES):
            raise ValueError("两次构建字节不一致；已拒绝更新。")
        _unchanged(project["hashes"])
        first = temporary / "first"
        report = {"profile": profile, "kind": "candidate" if profile == "candidate" else "release-rehearsal" if rehearsal else "formal-dist",
                  "releaseId": project["manifest"]["releaseId"], "destination": str(destination),
                  "byteIdentical": True, "outputSchemaValid": True, "validation": validation,
                  "hashes": {file: sha256_bytes((first / file).read_bytes()) for file in BUILD_FILES}}
        try:
            units = collect_units(project["documents"], project["display"], release_relative=project["release"].relative_to(root).as_posix())
            report["terminology"] = terminology_audit(root / "terminology/ipg-glossary.txt", units)
        except Exception as error:
            report["terminology"] = {"nonBlocking": True, "error": str(error)}
        if profile == "release" and not rehearsal:
            staged = temporary / "promote"
            staged.mkdir()
            for file in FINAL_FILES:
                shutil.copyfile(first / file, staged / file)
            lock = root / ".ipg-dist.lock"
            # Cooperative writers serialize; no per-file overwrites. Windows directory
            # replacement has a brief absent-path window, not a kernel rename-exchange.
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.close(descriptor)
                _unchanged(project["hashes"])
                report["publication"] = _publish_directory(staged, destination, temporary)
            finally:
                try:
                    lock.unlink(missing_ok=True)
                except OSError as error:
                    report["lockCleanupWarning"] = str(error)
        else:
            if destination.exists():
                raise ValueError("候选/演练输出目录已存在；请选择新目录，不会清空旧证据。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(first, destination)
            (destination / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Formal dist contains only the two final files; hashes are returned externally.
        return report


REASONS = {
    "missing-translation": "缺译", "missing-review-ledger": "缺少完整审校账本",
    "missing-review-record": "缺少审校记录", "unreviewed": "尚未审校", "stale-review": "审校记录已过期",
    "review-source-hash-mismatch": "审校原文哈希不一致", "review-target-hash-mismatch": "审校译文哈希不一致",
    "orphan-review-record": "存在已删除单元的孤立审校记录", "duplicate-review-record": "存在重复审校记录",
    "annotation-license-pending": "注解授权或署名尚未完成", "manifest-not-publishable": "manifest 尚未允许发布（publishable: false）",
    "manifest-not-full-document": "范围不是全文", "deferred-publication-annotations": "仍有延期发布注解",
    "review-ledger-invalid": "审校账本格式或版本不正确", "unresolved-mapping": "存在未决迁移映射",
}


def describe_validation(report: dict[str, Any]) -> str:
    if report["valid"]:
        return "正式发布条件已满足。" if report["profile"] == "release" else "候选结构检查通过；不表示可正式发布。"
    counts = Counter(item["code"] for item in [*report["structuralFindings"], *report["readinessFindings"]])
    return "\n".join(f"- {REASONS.get(code, '结构或来源检查失败 (' + code + ')')}：{count} 项" for code, count in sorted(counts.items()))


def _parser(name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=name)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    return parser


def validate_main(argv: list[str] | None = None) -> int:
    parser = _parser("ipg-validate")
    parser.add_argument("--profile", required=True, choices=("candidate", "release"))
    args = parser.parse_args(argv)
    try:
        report = validate_project(args.project_root, profile=args.profile)
        print(describe_validation(report))
        return 0 if report["valid"] else 1
    except (ValueError, OSError, KeyError) as error:
        print(f"检查已停止：{error}")
        return 1


def build_main(argv: list[str] | None = None) -> int:
    parser = _parser("ipg-build")
    parser.add_argument("--profile", required=True, choices=("candidate", "release"))
    parser.add_argument("--rehearsal", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.project_root / args.output if args.output else None
        report = build_project(args.project_root, profile=args.profile, rehearsal=args.rehearsal, output=output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f"构建已停止：{error}")
        return 1


def output_main(argv: list[str] | None = None) -> int:
    parser = _parser("ipg-validate-output")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args(argv)
    try:
        _, manifest = _current_manifest(args.project_root)
        path = args.input or args.project_root / "dist/rules.json"
        report = validate_output(path, args.project_root)
        if report["valid"] and load_json(path)["releaseId"] != manifest["releaseId"]:
            report["valid"] = False
            report["errors"].append("输出 releaseId 与当前 release 指针不一致。")
        print("输出 schema 验证通过。" if report["valid"] else "输出 schema 验证失败：\n" + "\n".join(report["errors"]))
        return 0 if report["valid"] else 1
    except (ValueError, OSError) as error:
        print(f"输出检查已停止：{error}")
        return 1
