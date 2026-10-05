"""Immutable, hash-addressed IPG update evidence under ignored outputs only."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from .core import ROOT, load_json, sha256_bytes
from .production import candidate_destination
from .reconcile import official_records


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def update_output(root: Path, path: Path) -> Path:
    path = candidate_destination(root, path)
    if not path.is_relative_to((root / "outputs/official-update").resolve()):
        raise ValueError("官方更新只能写入 outputs/official-update 隔离目录。")
    return path


def artifact_hashes(path: Path) -> dict[str, str]:
    hashes = {}
    for file in sorted(path.rglob("*")):
        if file.is_symlink() or file.is_junction():
            raise ValueError("快照禁止链接")
        if file.is_file() and file.name != "SHA256SUMS":
            hashes[file.relative_to(path).as_posix()] = sha256_bytes(file.read_bytes())
    return hashes


def verify_snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.is_junction():
        raise ValueError("快照禁止链接")
    rows = (path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    recorded = {}
    for row in rows:
        digest, name = row.split("  ", 1)
        if name in recorded or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("快照哈希清单无效")
        recorded[name] = digest
    if recorded != artifact_hashes(path):
        raise ValueError("不可变快照文件缺失、新增或遭到篡改")
    manifest = load_json(path / "snapshot.json")
    if manifest["snapshotVersion"] != 1 or manifest["contextHash"] != path.name:
        raise ValueError("快照身份不一致")
    if manifest["contextHash"] != sha256_bytes(json_bytes(manifest["context"])):
        raise ValueError("快照上下文哈希不一致")
    expected = manifest["artifacts"]
    actual = {k: v for k, v in recorded.items() if k != "snapshot.json"}
    if expected != actual:
        raise ValueError("快照 manifest 哈希清单不一致")
    official = manifest["official"]
    version = manifest["versions"]["official"]
    target = manifest["context"]["targetOfficial"]
    if (any(official[key] != target[key] for key in target)
            or any(official[key] != version[key] for key in ("effectiveDate", "pdfSha256"))):
        raise ValueError("快照官方版本轴与上下文不一致")
    pdf = (path / "official/IPG_EN.pdf").read_bytes()
    if not pdf.startswith(b"%PDF-") or sha256_bytes(pdf) != official["pdfSha256"]:
        raise ValueError("快照官方 PDF 不合法")
    parsed = load_json(path / "parsed/official.json")
    coverage = parsed["coverage"]
    if (parsed["pdf"]["sha256"] != official["pdfSha256"] or coverage["unclassifiedLineCount"]
            or coverage["lineCount"] != coverage["classifiedLineCount"]
            or len(coverage["lines"]) != coverage["lineCount"]):
        raise ValueError("快照解析或覆盖不完整")
    for record in official_records([parsed["document"]]):
        if record["kind"] != "block":
            continue
        units = record["node"]["officialPdfUnits"]
        if not units:
            raise ValueError("快照 block 缺少 PDF provenance")
        for unit in units:
            box = unit["bbox"]
            if (unit["pdfSha256"] != official["pdfSha256"] or not 1 <= unit["page"] <= parsed["pdf"]["pages"]
                    or len(box) != 4 or not box[0] < box[2] or not box[1] < box[3] or not unit["extractionUnit"]):
                raise ValueError("快照 PDF provenance 不合法")
    for name, digest in manifest["context"]["baseFiles"].items():
        if recorded.get("inputs/base/" + name) != digest:
            raise ValueError("快照基础输入与上下文不一致")
    for name, digest in manifest["context"]["implementation"].items():
        if recorded.get("implementation/" + name) != digest:
            raise ValueError("快照实现与上下文不一致")
    return manifest


def create_snapshot(root: Path, *, context: dict, official: dict, payloads: dict[str, bytes],
                    versions: dict) -> Path:
    date.fromisoformat(official["effectiveDate"])
    if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", official["effectiveDate"])
            or not re.fullmatch(r"[0-9a-f]{64}", official["pdfSha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", versions["annotations"]["sourceSha256"])
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", versions["translation"]["revision"])):
        raise ValueError("快照版本路径不合法")
    context_hash = sha256_bytes(json_bytes(context))
    destination = update_output(root, root / "outputs/official-update/snapshots/official" /
        official["effectiveDate"] / official["pdfSha256"] / "imports" /
        versions["annotations"]["sourceSha256"] / versions["translation"]["revision"] / context_hash)
    if destination.exists():
        stored = verify_snapshot(destination)
        if stored["context"] != context:
            raise ValueError("快照上下文不一致，禁止覆盖")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=destination.parent) as temporary:
        staged = Path(temporary) / context_hash
        staged.mkdir()
        for name, content in payloads.items():
            file = staged / name
            if not file.resolve().is_relative_to(staged.resolve()) or name in {"snapshot.json", "SHA256SUMS"}:
                raise ValueError("快照文件路径不合法")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        manifest = {"snapshotVersion": 1, "contextHash": context_hash, "context": context,
                    "official": official, "versions": versions, "artifacts": artifact_hashes(staged)}
        (staged / "snapshot.json").write_bytes(json_bytes(manifest))
        hashes = artifact_hashes(staged)
        (staged / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())), encoding="utf-8", newline="\n")
        verify_snapshot(staged)
        try:
            # rename never replaces a non-empty existing snapshot; racing writers verify it.
            os.rename(staged, destination)
        except OSError:
            if not destination.exists():
                raise
            verify_snapshot(destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ipg-snapshot")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.verify:
            path = update_output(args.project_root, args.project_root / args.verify)
            manifest = verify_snapshot(path)
            print(f"快照校验通过：{manifest['official']['effectiveDate']} / {manifest['official']['pdfSha256']}")
        else:
            from .official_update import snapshot_current
            result = snapshot_current(args.project_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"快照已停止：{error}")
        return 1
