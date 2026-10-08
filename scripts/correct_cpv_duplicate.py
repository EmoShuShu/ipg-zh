"""One-time correction of the maintainer-confirmed CPV transcription duplicate.

Run with OmegaT saved and closed. This is not a general English update command.
"""
from __future__ import annotations

import copy
import ctypes
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from ipg_pipeline import full_review as workflow
from ipg_pipeline.core import dump_yaml, load_json, load_yaml, sha256_bytes, sha256_text
from ipg_pipeline.omegat import _yaml_node_at_pointer, minimal_yaml_update, parse_po
from ipg_pipeline.review import refresh_review_ledger

UNIT_ID = "annotation-block:ipg-ann-3-7-60602d07adb5-g01-b02"
OLD_HASH = "8336bca2c2ce27804c73c1f049cd13cbdfdd8df3483a1d6f41bfc921c261f33a"
SENTENCE = "Any backup should only go to when the opponent acted on the incorrect information, and not before then."
REPORT_PATH = Path("docs/review/2026-10-06-cpv-duplicate-correction.md")


def replace_once(content: bytes, old: str, new: str) -> bytes:
    before, after = old.encode("utf-8"), new.encode("utf-8")
    if content.count(before) != 1:
        raise ValueError("correction must match exactly once")
    return content.replace(before, after, 1)


def correct_tmx(content: bytes, old: str, new: str) -> bytes:
    """Keep the XML bytes, all translations, notes, and other TUs unchanged."""
    expected = ET.fromstring(content)
    matches = []
    for tu in expected.findall(".//tu"):
        props = {prop.get("type"): prop.text for prop in tu.findall("prop")}
        for variant in tu.findall("tuv"):
            language = variant.get("lang", variant.get("{http://www.w3.org/XML/1998/namespace}lang", ""))
            segment = variant.find("seg")
            if language.lower().startswith("en") and segment is not None and segment.text == old:
                if props.get("path") != UNIT_ID:
                    raise ValueError("TMX source does not have the expected stable identity")
                matches.append(segment)
    if len(matches) != 1:
        raise ValueError("TMX correction must identify exactly one English segment")
    matches[0].text = new
    updated = replace_once(content, escape(old), escape(new))
    if ET.tostring(ET.fromstring(updated)) != ET.tostring(expected):
        raise ValueError("TMX correction changed unrelated data")
    return updated


def ensure_closed(project: Path) -> None:
    if os.name != "nt":
        return
    from ctypes import wintypes

    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(project / "omegat.project"), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError("Save and close OmegaT before correcting the source")
    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.windll.kernel32.CloseHandle(handle)


def apply_correction() -> dict:
    root, project = workflow.ROOT, workflow.PROJECT_DIR
    ensure_closed(project)
    inspection = workflow.inspect_target_project()
    mapping = copy.deepcopy(inspection["mapping"])
    unit = next(item for item in mapping["units"] if item["id"] == UNIT_ID)
    old = unit["source"]
    if sha256_text(old) != OLD_HASH or old.count(SENTENCE) != 2:
        raise ValueError("Expected original CPV source is absent; do not apply twice")
    new = old.replace(f"{SENTENCE} {SENTENCE}", SENTENCE, 1)
    if new.count(SENTENCE) != 1:
        raise ValueError("Duplicate is not the expected adjacent pair")
    timestamp = workflow._now()
    report = root / REPORT_PATH
    if report.exists():
        raise ValueError("Correction report already exists")
    edits: dict[Path, bytes] = {}
    source = root / unit["file"]
    with tempfile.TemporaryDirectory(prefix="ipg-cpv-correction-") as temporary:
        staged = Path(temporary) / source.name
        shutil.copyfile(source, staged)
        minimal_yaml_update(staged, [*unit["pointer"][:-1], "en"], old, new)
        edits[source] = staged.read_bytes()

    registry_path = root / "src/ipg/id-registry.yaml"
    registry = load_yaml(registry_path)
    index = next(i for i, entry in enumerate(registry["entries"]) if entry["id"] == UNIT_ID.split(":", 1)[1])
    event = {"event": "updated", "atVersion": mapping["releaseId"], "recordedAt": timestamp,
             "reason": "Maintainer confirmed accidental duplicate during transcription, not an upstream annotation error",
             "changes": {"englishHash": {"from": OLD_HASH, "to": sha256_text(new)}}}
    registry["entries"][index]["history"].append(event)
    text = registry_path.read_bytes().decode("utf-8")
    history = _yaml_node_at_pointer(yaml.compose(text), ["entries", index, "history"])
    insertion = text.rfind("\n", 0, history.end_mark.index) + 1
    addition = "".join("  " + line + "\n" for line in dump_yaml([event]).splitlines())
    updated_registry = text[:insertion] + addition + text[insertion:]
    if yaml.safe_load(updated_registry) != registry:
        raise ValueError("Registry insertion did not preserve original evidence")
    edits[registry_path] = updated_registry.encode("utf-8")

    for directory in ("source", "target"):
        path = project / directory / unit["po"]
        content = replace_once(path.read_bytes(), json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False))
        if OLD_HASH.encode() in content:
            content = replace_once(content, OLD_HASH, sha256_text(new))
        edits[path] = content
    tmx_paths = [project / "omegat/project_save.tmx", *sorted(project.glob("*.tmx"))]
    for path in tmx_paths:
        edits[path] = correct_tmx(path.read_bytes(), old, new)

    unit["source"], unit["sourceHash"] = new, sha256_text(new)
    mapping["sourceFiles"][unit["file"]] = sha256_bytes(edits[source])
    mapping["sourcePoHashes"][unit["po"]] = sha256_bytes(edits[project / "source" / unit["po"]])
    encode_json = lambda data: (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    edits[workflow.MAPPING_PATH] = encode_json(mapping)
    ledger_before = load_json(workflow.REVIEW_LEDGER_PATH)
    ledger = refresh_review_ledger(workflow._current_units(mapping), ledger_before,
                                  release_id=mapping["releaseId"], translation_revision=mapping["translationRevision"])
    if [a["unitId"] for a, b in zip(ledger["entries"], ledger_before["entries"]) if a != b] != [UNIT_ID]:
        raise ValueError("Correction would change other review records")
    edits[workflow.REVIEW_LEDGER_PATH] = encode_json(ledger)
    # These derived files can be rewritten by the existing build/report helpers.
    generated = [workflow.PROGRESS_JSON, workflow.PROGRESS_MARKDOWN,
                 *(workflow.CURRENT_CANDIDATE / name for name in
                   ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json", "validation.json"))]
    before = {path: path.read_bytes() if path.exists() else None for path in [*edits, *generated, report]}
    for path in before:
        path.resolve().relative_to(root.resolve())
    backup = root / "outputs" / f"cpv-duplicate-backup-{timestamp.replace(':', '').replace('+', '_')}"
    backup.mkdir(parents=True, exist_ok=False)
    for directory in ("src", "review", "terminology"):
        shutil.copytree(root / directory, backup / directory)
    shutil.copytree(project, backup / project.relative_to(root))
    if workflow.CURRENT_CANDIDATE.exists():
        shutil.copytree(workflow.CURRENT_CANDIDATE, backup / workflow.CURRENT_CANDIDATE.relative_to(root))
    for path in generated:
        if path.exists() and path.parent != workflow.CURRENT_CANDIDATE:
            dest = backup / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
    ensure_closed(project)
    if any((path.read_bytes() if path.exists() else None) != data for path, data in before.items()):
        raise ValueError("Inputs changed during backup; no source edits applied")
    try:
        for path, content in edits.items():
            path.write_bytes(content)
        after = workflow.inspect_target_project()
        if after["modifiedPo"] != inspection["modifiedPo"]:
            raise ValueError("Pending translation edits were not preserved")
        for name in workflow.PO_ORDER:
            previous = inspection["entries"][name]
            current = after["entries"][name]
            expected = [{**entry, "source": new} if entry["id"] == UNIT_ID else entry for entry in previous]
            if current != expected:
                raise ValueError("Target PO changed outside the corrected source")
        validation = workflow.validate_current_state()
        candidate = workflow.build_current_candidate(validation)
        progress = workflow.refresh_progress_reports()
        if len(mapping["units"]) != 1007 or progress["initialMissingRemaining"] != len(mapping["initialMissingUnitIds"]):
            raise ValueError("Unit count or missing translations changed")
        body = ("# CPV 英文录入重复勘误\n\n"
                f"- 日期：{timestamp}\n- 单元：`{UNIT_ID}`\n"
                "- 原因：维护者确认是录入时重复，不是注解作者的原文错误。\n"
                "- 修改：仅删除相邻重复的第一句中的一次；中文、稳定 ID、锚点和旧源定位保留。\n"
                f"- 旧英文 SHA-256：`{OLD_HASH}`\n- 新英文 SHA-256：`{sha256_text(new)}`\n"
                "- 登记表保留原始 allocated 历史及迁移 evidence，追加 updated 勘误历史。\n"
                "- 同步：第三章 YAML、source/target PO、工程 TMX 与三份导出 TMX、映射基线和审校账本。\n"
                "- 本单元原已审状态因英文变化而失效，现为 stale；需重新人工核对。其他审校状态保留。\n"
                "- 原始 AIPG、旧源快照、历史迁移证据、备份及官方版本身份均保留。\n"
                f"- 完整本地备份：`{backup.relative_to(root).as_posix()}`。\n"
                "- 校验：OmegaT 基线与八份 target PO 通过；所有 target 中文和 TMX 批注保留；"
                "candidate 验证通过，连续两次构建逐字节一致；审校进度已刷新。\n\n"
                "这是一次特定的本地录入勘误。复现脚本为 `scripts/correct_cpv_duplicate.py`，"
                "只接受指定单元的原始英文哈希，并拒绝重复执行。\n")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(body, encoding="utf-8", newline="\n")
    except Exception:
        for path, content in before.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(content)
        raise
    return {"unitId": UNIT_ID, "sourceHash": sha256_text(new), "backup": str(backup),
            "report": str(report), "candidateValid": candidate["candidate"]["valid"],
            "reviewCounts": progress["counts"], "pendingModifiedPo": after["modifiedPo"]}


if __name__ == "__main__":
    print(json.dumps(apply_correction(), ensure_ascii=False, indent=2))
