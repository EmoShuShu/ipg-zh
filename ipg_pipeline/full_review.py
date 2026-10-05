from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .builder import VERSION_NOTES_PATH, build_outputs
from .core import (
    ROOT,
    dump_yaml,
    load_json,
    load_yaml,
    sha256_bytes,
    sha256_text,
    validate_registry,
    validate_schema,
)
from .omegat import collect_units, parse_po, render_po, validate_target_entries
from .omegat import minimal_yaml_update
from .p4 import RELEASE_DIR
from .review import (
    extract_omegat_notes,
    record_reviewed_units,
    refresh_review_ledger,
    review_status_report,
    terminology_audit,
)
from .validation import validate_release


PROJECT_DIR = ROOT / "outputs/omegat-ipg-full"
MAPPING_PATH = PROJECT_DIR / "omegat/full-review.mapping.json"
GLOSSARY_PATH = ROOT / "terminology/ipg-glossary.txt"
PO_ORDER = [
    "display-values.po",
    "front-matter.po",
    "chapter-01.po",
    "chapter-02.po",
    "chapter-03.po",
    "chapter-04.po",
    "appendix-a.po",
    "appendix-b.po",
]
EXPECTED_PO_COUNTS = {
    "display-values.po": 15,
    "front-matter.po": 7,
    "chapter-01.po": 140,
    "chapter-02.po": 302,
    "chapter-03.po": 278,
    "chapter-04.po": 223,
    "appendix-a.po": 24,
    "appendix-b.po": 18,
}
EXPECTED_UNIT_COUNT = 1007
TRANSLATION_REVISION = "zh-r0001"
REVIEW_LEDGER_PATH = ROOT / f"review/status/{TRANSLATION_REVISION}.json"
TRANSLATION_NOTES_PATH = ROOT / f"review/translation-notes/{TRANSLATION_REVISION}.json"
REVIEW_ACTIONS_PATH = ROOT / f"review/actions/{TRANSLATION_REVISION}.yaml"
CURRENT_CANDIDATE = ROOT / "outputs/current-candidate"
PROGRESS_JSON = ROOT / "outputs/review-status.json"
PROGRESS_MARKDOWN = ROOT / "outputs/review-status.md"
TERMINOLOGY_JSON = ROOT / "outputs/terminology-audit.json"
TERMINOLOGY_MARKDOWN = ROOT / "outputs/terminology-audit.md"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, content: Any) -> None:
    _write_text(path, json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def full_inputs() -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [(filename, load_yaml(RELEASE_DIR / filename)) for filename in manifest["documents"]]
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    return manifest, documents, display


def units_by_po(
    documents: list[tuple[str, dict[str, Any]]], display: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    result = {name: [] for name in PO_ORDER}
    for unit in collect_units(documents, display):
        po_name = (
            "display-values.po"
            if unit["kind"] == "display-value"
            else Path(unit["file"]).name.replace(".yaml", ".po")
        )
        result[po_name].append(unit)
    counts = {name: len(units) for name, units in result.items()}
    if counts != EXPECTED_PO_COUNTS or sum(counts.values()) != EXPECTED_UNIT_COUNT:
        raise ValueError(f"full OmegaT unit contract changed: {counts}")
    return result


def _project_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<omegat>
  <project version="1.0">
    <source_dir>source</source_dir>
    <target_dir>target</target_dir>
    <tm_dir>tm</tm_dir>
    <glossary_dir>../../terminology</glossary_dir>
    <glossary_file>ipg-glossary.txt</glossary_file>
    <dictionary_dir>dictionary</dictionary_dir>
    <source_lang>EN-US</source_lang>
    <target_lang>ZH-CN</target_lang>
    <source_tok>org.omegat.tokenizer.LuceneEnglishTokenizer</source_tok>
    <target_tok>org.omegat.tokenizer.LuceneSmartChineseTokenizer</target_tok>
    <sentence_seg>false</sentence_seg>
    <support_default_translations>false</support_default_translations>
    <remove_tags>false</remove_tags>
    <external_command></external_command>
    <repositories />
  </project>
</omegat>
"""


def _filters_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<filters removeTags="false" removeSpacesNonseg="false" preserveSpaces="true" ignoreFileContext="false">
  <filter className="org.omegat.filters2.po.PoFilter" enabled="true">
    <files targetEncoding="UTF-8" sourceEncoding="UTF-8" targetFilenamePattern="${filename}" sourceFilenameMask="*.po" />
    <option name="skipHeader" value="true" />
  </filter>
</filters>
"""


def _project_readme() -> str:
    return """# IPG 全文 OmegaT 审校项目

本目录由“审校助手”管理。请双击 `omegat.project` 打开同一个项目，8 个 PO
文件会在一次全文搜索中共同参与检索。项目关闭句子分段，以段落为审校单位。

完成一批审校后，请在 OmegaT 中选择“项目 → 创建已译文档”，再回到审校助手
选择“完成审校并生成候选阅读文档”。不要手工替换 `source/` 中的 PO。

项目直接使用仓库的 `terminology/ipg-glossary.txt`；Ctrl+Shift+G 添加的词条应
写入该文件。`target/` 初始为空，只有 OmegaT 创建已译文档后才会出现文件。
"""


def _source_file_hashes(units: list[dict[str, Any]]) -> dict[str, str]:
    return {
        relative: sha256_bytes((ROOT / relative).read_bytes())
        for relative in sorted({unit["file"] for unit in units})
    }


def validate_repository_inputs() -> dict[str, Any]:
    manifest, documents, display = full_inputs()
    errors = validate_registry(load_yaml(ROOT / "src/ipg/id-registry.yaml"))
    if errors:
        raise ValueError("repository validation failed: " + "; ".join(errors))
    structural = validate_release(
        profile="candidate", manifest=manifest, documents=[item[1] for item in documents],
        display_values=display, migration_report=_migration_gate_stub(), review_ledger=None,
    )
    if not structural["valid"]:
        raise ValueError("repository structural validation failed: " + json.dumps(structural["structuralFindings"], ensure_ascii=False))
    grouped = units_by_po(documents, display)
    units = [unit for name in PO_ORDER for unit in grouped[name]]
    with tempfile.TemporaryDirectory(prefix="ipg-review-output-") as temporary:
        output = Path(temporary)
        build_outputs(output, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate", source_root=ROOT)
        output_errors = validate_schema(load_json(output / "rules.json"), ROOT / "schema/ipg-output.schema.json")
        if output_errors:
            raise ValueError("output schema validation failed: " + "; ".join(output_errors))
    glossary = terminology_audit(GLOSSARY_PATH, units)
    return {"manifest": manifest, "documents": documents, "display": display, "grouped": grouped, "units": units, "glossary": glossary}


def _new_mapping(inputs: dict[str, Any]) -> dict[str, Any]:
    initial_missing = [unit["id"] for unit in inputs["units"] if not unit["target"]]
    return {
        "schemaVersion": 1,
        "releaseId": inputs["manifest"]["releaseId"],
        "translationRevision": inputs["manifest"]["versions"]["translation"]["revision"],
        "poOrder": PO_ORDER,
        "poCounts": EXPECTED_PO_COUNTS,
        "initialMissingUnitIds": initial_missing,
        "units": [
            {
                **unit,
                "po": po_name,
                "sourceHash": sha256_text(unit["source"]),
                "targetHash": sha256_text(unit["target"]),
            }
            for po_name in PO_ORDER
            for unit in inputs["grouped"][po_name]
        ],
        "sourceFiles": _source_file_hashes(inputs["units"]),
        "sourcePoHashes": {
            po_name: sha256_text(render_po(inputs["grouped"][po_name]))
            for po_name in PO_ORDER
        },
    }


def _validate_existing_project(inputs: dict[str, Any]) -> dict[str, Any]:
    if not (PROJECT_DIR / "omegat.project").is_file():
        raise ValueError(
            "OmegaT 项目目录已存在但 omegat.project 缺失；请人工检查或将整个目录移走后重试。"
        )
    if not MAPPING_PATH.is_file():
        raise ValueError("OmegaT 项目缺少基线映射文件；请人工检查，不会自动覆盖。")
    mapping = load_json(MAPPING_PATH)
    actual_po = sorted(path.name for path in (PROJECT_DIR / "source").glob("*.po"))
    if actual_po != sorted(PO_ORDER):
        raise ValueError(f"source PO 集合不完整或多出文件：{actual_po}")
    expected_by_id = {unit["id"]: unit for unit in mapping["units"]}
    current_by_id = {unit["id"]: unit for unit in inputs["units"]}
    if set(expected_by_id) != set(current_by_id) or len(expected_by_id) != EXPECTED_UNIT_COUNT:
        raise ValueError("正式 source 与 OmegaT 稳定 ID 集合不兼容；不会重新导出覆盖。")
    for unit_id, current in current_by_id.items():
        baseline = expected_by_id[unit_id]
        if baseline["sourceHash"] != sha256_text(current["source"]):
            raise ValueError(f"正式英文已经变化：{unit_id}；请先处理版本协调。")
        if baseline["targetHash"] != sha256_text(current["target"]):
            raise ValueError(f"正式中文与 OmegaT 回写基线不一致：{unit_id}")
    for relative, expected_hash in mapping["sourceFiles"].items():
        if sha256_bytes((ROOT / relative).read_bytes()) != expected_hash:
            raise ValueError(f"正式 YAML 在 OmegaT 基线之外发生变化：{relative}")
    for po_name in PO_ORDER:
        source_path = PROJECT_DIR / "source" / po_name
        if sha256_bytes(source_path.read_bytes()) != mapping["sourcePoHashes"][po_name]:
            raise ValueError(f"source PO 已变化：{po_name}；不会自动覆盖。")
        errors = validate_target_entries(
            {"units": [unit for unit in mapping["units"] if unit["po"] == po_name]},
            parse_po(source_path),
        )
        if errors:
            raise ValueError(json.dumps(errors, ensure_ascii=False, sort_keys=True))
    return mapping


def prepare_full_project() -> dict[str, Any]:
    inputs = validate_repository_inputs()
    if PROJECT_DIR.exists():
        mapping = _validate_existing_project(inputs)
        return {
            "created": False,
            "project": str(PROJECT_DIR / "omegat.project"),
            "unitCount": len(mapping["units"]),
            "poCounts": mapping["poCounts"],
            "glossary": inputs["glossary"],
        }

    for name in ("source", "target", "tm", "dictionary", "omegat"):
        (PROJECT_DIR / name).mkdir(parents=True, exist_ok=True)
    mapping = _new_mapping(inputs)
    for po_name in PO_ORDER:
        _write_text(PROJECT_DIR / "source" / po_name, render_po(inputs["grouped"][po_name]))
    _write_text(PROJECT_DIR / "omegat.project", _project_xml())
    _write_text(PROJECT_DIR / "omegat/filters.xml", _filters_xml())
    _write_text(PROJECT_DIR / "README.md", _project_readme())
    _write_json(MAPPING_PATH, mapping)
    return {
        "created": True,
        "project": str(PROJECT_DIR / "omegat.project"),
        "unitCount": len(mapping["units"]),
        "poCounts": mapping["poCounts"],
        "glossary": inputs["glossary"],
    }


def _current_units(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": unit["id"],
            "source": unit["source"],
            "target": unit["target"],
            "po": unit["po"],
        }
        for unit in mapping["units"]
    ]


def initialize_full_review_state() -> dict[str, Any]:
    """Create current review state without importing P2 demonstration data."""
    mapping = _validate_existing_project(validate_repository_inputs())
    existing = load_json(REVIEW_LEDGER_PATH) if REVIEW_LEDGER_PATH.exists() else None
    current_ids = {unit["id"] for unit in mapping["units"]}
    if existing is not None:
        errors = validate_schema(existing, ROOT / "schema/review-ledger.schema.json")
        ledger_ids = [entry["unitId"] for entry in existing.get("entries", [])]
        if errors or len(ledger_ids) != len(set(ledger_ids)) or set(ledger_ids) != current_ids:
            raise ValueError(
                "现有全文审校账本与当前单元不兼容；不会静默修补或覆盖。"
            )
    ledger = refresh_review_ledger(
        _current_units(mapping),
        existing,
        release_id=mapping["releaseId"],
        translation_revision=mapping["translationRevision"],
    )
    errors = validate_schema(ledger, ROOT / "schema/review-ledger.schema.json")
    if errors:
        raise ValueError("review ledger schema failed: " + "; ".join(errors))
    _write_json(REVIEW_LEDGER_PATH, ledger)
    if TRANSLATION_NOTES_PATH.exists():
        notes = load_json(TRANSLATION_NOTES_PATH)
        errors = validate_schema(notes, ROOT / "schema/translation-notes.schema.json")
        note_ids = [note["unitId"] for note in notes.get("notes", [])]
        if errors or not set(note_ids) <= current_ids:
            raise ValueError("现有翻译批注库与当前单元不兼容；不会静默覆盖。")
    else:
        _write_json(
            TRANSLATION_NOTES_PATH,
            {
                "schemaVersion": 1,
                "releaseId": mapping["releaseId"],
                "translationRevision": mapping["translationRevision"],
                "notes": [],
            },
        )
    if not REVIEW_ACTIONS_PATH.exists():
        _write_text(
            REVIEW_ACTIONS_PATH,
            dump_yaml(
                {
                    "schemaVersion": 1,
                    "releaseId": mapping["releaseId"],
                    "translationRevision": mapping["translationRevision"],
                    "scope": "full-document-review",
                    "actions": [],
                }
            ),
        )
    return ledger


def _write_terminology_markdown(report: dict[str, Any]) -> None:
    lines = [
        "# 术语审计（非阻塞）",
        "",
        "本报告只供人工参考；其中的警告不会阻止已验证的回写或候选构建。",
        "",
        f"- 词汇表格式问题：{report.get('glossaryIssueCount', 0)}",
        f"- 术语提示：{report.get('findingCount', 0)}",
    ]
    if report.get("error"):
        lines.extend(["", f"- 审计程序错误：{report['error']}"])
    _write_text(TERMINOLOGY_MARKDOWN, "\n".join(lines) + "\n")


def run_nonblocking_terminology_audit() -> dict[str, Any]:
    try:
        inputs = validate_repository_inputs()
        report = inputs["glossary"]
    except Exception as error:  # The audit is deliberately advisory.
        report = {
            "schemaVersion": 1,
            "nonBlocking": True,
            "glossaryIssueCount": 0,
            "findingCount": 0,
            "glossaryIssues": [],
            "findings": [],
            "error": str(error),
        }
    _write_json(TERMINOLOGY_JSON, report)
    _write_terminology_markdown(report)
    return report


def _write_progress_markdown(report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# IPG 全文审校进度",
        "",
        f"- 总单元：{report['total']}",
        f"- 已审未改：{counts['reviewed-unchanged']}",
        f"- 已审有改：{counts['reviewed-modified']}",
        f"- 已过期：{counts['stale']}",
        f"- 未审：{counts['unreviewed']}",
        f"- 12 项初始缺译中尚余：{report['initialMissingRemaining']}",
        "",
        "| PO 文件 | 总数 | 已审未改 | 已审有改 | stale | 未审 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for po_name in PO_ORDER:
        item = report["byPo"][po_name]
        lines.append(
            f"| {po_name} | {item['total']} | {item['reviewed-unchanged']} | "
            f"{item['reviewed-modified']} | {item['stale']} | {item['unreviewed']} |"
        )
    _write_text(PROGRESS_MARKDOWN, "\n".join(lines) + "\n")


def refresh_progress_reports() -> dict[str, Any]:
    mapping = _validate_existing_project(validate_repository_inputs())
    ledger = refresh_review_ledger(
        _current_units(mapping),
        load_json(REVIEW_LEDGER_PATH) if REVIEW_LEDGER_PATH.exists() else None,
        release_id=mapping["releaseId"],
        translation_revision=mapping["translationRevision"],
    )
    status = review_status_report(ledger)
    po_for_id = {unit["id"]: unit["po"] for unit in mapping["units"]}
    by_po = {}
    for po_name in PO_ORDER:
        entries = [entry for entry in ledger["entries"] if po_for_id[entry["unitId"]] == po_name]
        counts = Counter(entry["status"] for entry in entries)
        by_po[po_name] = {
            "total": len(entries),
            **{
                name: counts.get(name, 0)
                for name in ("reviewed-unchanged", "reviewed-modified", "stale", "unreviewed")
            },
        }
    report = {
        **status,
        "releaseId": mapping["releaseId"],
        "translationRevision": mapping["translationRevision"],
        "initialMissingTotal": len(mapping["initialMissingUnitIds"]),
        "initialMissingRemaining": sum(
            not unit["target"]
            for unit in mapping["units"]
            if unit["id"] in set(mapping["initialMissingUnitIds"])
        ),
        "byPo": by_po,
    }
    _write_json(PROGRESS_JSON, report)
    _write_progress_markdown(report)
    return report


def _target_entries(mapping: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    target_dir = PROJECT_DIR / "target"
    actual = sorted(path.name for path in target_dir.iterdir() if path.is_file())
    if actual != sorted(PO_ORDER):
        raise ValueError(
            f"target PO 必须恰好包含 8 个文件；当前为 {actual}。请在 OmegaT 中创建已译文档。"
        )
    result = {}
    for po_name in PO_ORDER:
        entries = parse_po(target_dir / po_name)
        expected = {"units": [unit for unit in mapping["units"] if unit["po"] == po_name]}
        errors = validate_target_entries(expected, entries)
        if errors:
            raise ValueError(json.dumps({po_name: errors}, ensure_ascii=False, sort_keys=True))
        result[po_name] = entries
    return result


def modified_po_files(mapping: dict[str, Any], entries: dict[str, list[dict[str, str]]]) -> list[str]:
    baseline = {unit["id"]: unit["target"] for unit in mapping["units"]}
    return [
        po_name
        for po_name in PO_ORDER
        if any(item["target"] != baseline[item["id"]] for item in entries[po_name])
    ]


def inspect_target_project() -> dict[str, Any]:
    inputs = validate_repository_inputs()
    mapping = _validate_existing_project(inputs)
    entries = _target_entries(mapping)
    return {
        "mapping": mapping,
        "entries": entries,
        "modifiedPo": modified_po_files(mapping, entries),
    }


def _load_overlay(relative: str, candidate_root: Path) -> dict[str, Any]:
    candidate = candidate_root / relative
    return load_yaml(candidate if candidate.exists() else ROOT / relative)


def _leaf_differences(before: Any, after: Any, path: tuple[Any, ...] = ()) -> set[tuple[Any, ...]]:
    if isinstance(before, dict) and isinstance(after, dict):
        if set(before) != set(after):
            return {path}
        return set().union(
            *(_leaf_differences(before[key], after[key], (*path, key)) for key in before)
        )
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return {path}
        return set().union(
            *(_leaf_differences(old, new, (*path, index)) for index, (old, new) in enumerate(zip(before, after, strict=True)))
        )
    return set() if before == after else {path}


def _overlay_inputs(candidate_root: Path) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [
        (
            filename,
            _load_overlay(
                f"src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001/{filename}",
                candidate_root,
            ),
        )
        for filename in manifest["documents"]
    ]
    display = _load_overlay("src/ipg/display-values.yaml", candidate_root)
    return manifest, documents, display


def _migration_gate_stub() -> dict[str, Any]:
    return {
        "coverage": {
            "rawUnitCount": 4085,
            "disposedUnitCount": 4085,
            "duplicateConsumption": 0,
            "dispositions": {},
        },
        "findings": [],
    }


def _validate_candidate_overlay(candidate_root: Path) -> dict[str, Any]:
    manifest, documents, display = _overlay_inputs(candidate_root)
    candidate = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=[item[1] for item in documents],
        display_values=display,
        migration_report=_migration_gate_stub(),
        review_ledger=None,
    )
    if not candidate["valid"]:
        raise ValueError("candidate validation failed")
    first = candidate_root / ".build-first"
    second = candidate_root / ".build-second"
    build_outputs(first, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate", source_root=ROOT)
    build_outputs(second, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate", source_root=ROOT)
    names = ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json")
    if any((first / name).read_bytes() != (second / name).read_bytes() for name in names):
        raise ValueError("candidate build is not byte deterministic")
    output_errors = validate_schema(load_json(first / "rules.json"), ROOT / "schema/ipg-output.schema.json")
    if output_errors:
        raise ValueError("candidate output schema failed: " + "; ".join(output_errors))
    shutil.rmtree(first)
    shutil.rmtree(second)
    return candidate


def _tmx_hash() -> str | None:
    path = PROJECT_DIR / "omegat/project_save.tmx"
    return sha256_bytes(path.read_bytes()) if path.exists() else None


def _units_with_target_entries(
    mapping: dict[str, Any], entries: dict[str, list[dict[str, str]]]
) -> list[dict[str, Any]]:
    targets = {entry["id"]: entry["target"] for items in entries.values() for entry in items}
    return [
        {
            "id": unit["id"],
            "source": unit["source"],
            "target": targets[unit["id"]],
        }
        for unit in mapping["units"]
    ]


def extract_project_translation_notes(
    mapping: dict[str, Any],
    entries: dict[str, list[dict[str, str]]] | None = None,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    tmx = PROJECT_DIR / "omegat/project_save.tmx"
    if not tmx.exists():
        return {
            "schemaVersion": 1,
            "releaseId": mapping["releaseId"],
            "translationRevision": mapping["translationRevision"],
            "notes": [],
        }
    units = (
        _units_with_target_entries(mapping, entries)
        if entries is not None
        else _current_units(mapping)
    )
    notes = extract_omegat_notes(
        tmx,
        units,
        release_id=mapping["releaseId"],
        translation_revision=mapping["translationRevision"],
        recorded_at=recorded_at,
    )
    errors = validate_schema(notes, ROOT / "schema/translation-notes.schema.json")
    if errors:
        raise ValueError("translation notes schema failed: " + "; ".join(errors))
    return notes


def preview_full_writeback(
    selected_po: list[str],
    candidate_root: Path,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    inspection = inspect_target_project()
    mapping = inspection["mapping"]
    entries = inspection["entries"]
    version_notes_hash = sha256_bytes((ROOT / VERSION_NOTES_PATH).read_bytes())
    selected = [name for name in PO_ORDER if name in set(selected_po)]
    if not selected:
        raise ValueError("没有选择任何已经逐条审完的 PO")
    unknown = sorted(set(selected_po) - set(PO_ORDER))
    if unknown:
        raise ValueError(f"未知 PO：{unknown}")
    unselected_modified = [name for name in inspection["modifiedPo"] if name not in selected]
    if unselected_modified:
        raise ValueError(f"未选择的 PO 中存在文字修改：{unselected_modified}")

    if candidate_root.exists():
        shutil.rmtree(candidate_root)
    candidate_root.mkdir(parents=True)
    baseline = {unit["id"]: unit for unit in mapping["units"]}
    target = {item["id"]: item for po_name in PO_ORDER for item in entries[po_name]}
    changes = []
    derived_changes = {}
    allowed_differences: dict[str, set[tuple[Any, ...]]] = {}
    for po_name in selected:
        for item in entries[po_name]:
            unit = baseline[item["id"]]
            if item["target"] == unit["target"]:
                continue
            relative = unit["file"]
            candidate_file = candidate_root / relative
            if not candidate_file.exists():
                candidate_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, candidate_file)
            minimal_yaml_update(candidate_file, unit["pointer"], unit["target"], item["target"])
            allowed_differences.setdefault(relative, set()).add(tuple(unit["pointer"]))
            changes.append(
                {
                    "unitId": unit["id"],
                    "po": po_name,
                    "file": relative,
                    "pointer": unit["pointer"],
                    "oldTarget": unit["target"],
                    "newTarget": item["target"],
                    "oldTargetHash": unit["targetHash"],
                    "newTargetHash": sha256_text(item["target"]),
                }
            )
    for relative, allowed in allowed_differences.items():
        # Parent Chinese is the deterministic aggregate of independently reviewed fragments.
        for pointer in list(allowed):
            if "readingSegments" not in pointer:
                continue
            parent_pointer = list(pointer[:pointer.index("readingSegments")])
            before = load_yaml(ROOT / relative)
            after = load_yaml(candidate_root / relative)
            original, block = before, after
            for part in parent_pointer:
                original, block = original[part], block[part]
            aggregate = "\n\n".join(segment["text"]["zh"] for segment in block["readingSegments"])
            if aggregate != original["text"]["zh"]:
                minimal_yaml_update(candidate_root / relative, [*parent_pointer, "text", "zh"], block["text"]["zh"], aggregate)
                allowed.add(tuple([*parent_pointer, "text", "zh"]))
                derived_changes[(relative, tuple(parent_pointer))] = {
                    "file": relative, "pointer": [*parent_pointer, "text", "zh"],
                    "oldTarget": original["text"]["zh"], "newTarget": aggregate,
                    "reason": "aggregate of selected reading segment translations",
                }
        actual = _leaf_differences(load_yaml(ROOT / relative), load_yaml(candidate_root / relative))
        if actual != allowed:
            raise ValueError(f"candidate changed fields outside selected zh scalars: {relative}")
    validation = _validate_candidate_overlay(candidate_root)
    if sha256_bytes((ROOT / VERSION_NOTES_PATH).read_bytes()) != version_notes_hash:
        raise ValueError("版本说明在预览构建期间发生变化；请重新预览。")
    target_hashes = {
        name: sha256_bytes((PROJECT_DIR / "target" / name).read_bytes()) for name in PO_ORDER
    }
    selected_ids = {
        unit["id"] for unit in mapping["units"] if unit["po"] in selected
    }
    notes = extract_project_translation_notes(mapping, entries, recorded_at=recorded_at)
    missing_remaining = sum(not target[unit_id]["target"] for unit_id in mapping["initialMissingUnitIds"])
    return {
        "schemaVersion": 1,
        "selectedPo": selected,
        "reviewedUnitCount": len(selected_ids),
        "actualChangeCount": len(changes),
        "reviewedUnchangedCount": len(selected_ids) - len(changes),
        "noteUnitCount": sum(note["unitId"] in selected_ids for note in notes["notes"]),
        "initialMissingRemaining": missing_remaining,
        "changes": changes,
        "derivedChanges": [derived_changes[key] for key in sorted(derived_changes)],
        "expectedYamlScalarChangeCount": len(changes) + len(derived_changes),
        "mappingSha256": sha256_bytes(MAPPING_PATH.read_bytes()),
        "targetPoHashes": target_hashes,
        "tmxSha256": _tmx_hash(),
        "baseFiles": {
            **mapping["sourceFiles"],
            VERSION_NOTES_PATH.as_posix(): version_notes_hash,
        },
        "candidateFiles": {
            relative: sha256_bytes((candidate_root / relative).read_bytes())
            for relative in sorted(allowed_differences)
        },
        "candidateValidation": validation,
    }


def apply_full_writeback(preview: dict[str, Any], candidate_root: Path) -> dict[str, Any]:
    if sha256_bytes(MAPPING_PATH.read_bytes()) != preview["mappingSha256"]:
        raise ValueError("OmegaT mapping changed after preview")
    if _tmx_hash() != preview["tmxSha256"]:
        raise ValueError("project_save.tmx changed after preview")
    for po_name, expected in preview["targetPoHashes"].items():
        if sha256_bytes((PROJECT_DIR / "target" / po_name).read_bytes()) != expected:
            raise ValueError(f"target PO changed after preview: {po_name}")
    for relative, expected in preview["baseFiles"].items():
        if sha256_bytes((ROOT / relative).read_bytes()) != expected:
            if relative == VERSION_NOTES_PATH.as_posix():
                raise ValueError("version notes changed after preview; please preview again")
            raise ValueError(f"formal YAML changed after preview: {relative}")
    for relative, expected in preview["candidateFiles"].items():
        if sha256_bytes((candidate_root / relative).read_bytes()) != expected:
            raise ValueError(f"temporary candidate changed after preview: {relative}")

    before = {
        relative: sha256_bytes((ROOT / relative).read_bytes())
        for relative in preview["candidateFiles"]
    }
    for relative in sorted(preview["candidateFiles"]):
        shutil.copyfile(candidate_root / relative, ROOT / relative)
    after = {
        relative: sha256_bytes((ROOT / relative).read_bytes())
        for relative in preview["candidateFiles"]
    }

    mapping = load_json(MAPPING_PATH)
    changes = {change["unitId"]: change for change in preview["changes"]}
    for unit in mapping["units"]:
        change = changes.get(unit["id"])
        if change:
            unit["target"] = change["newTarget"]
            unit["targetHash"] = change["newTargetHash"]
    mapping["sourceFiles"] = _source_file_hashes(mapping["units"])
    _write_json(MAPPING_PATH, mapping)
    return {
        "schemaVersion": 1,
        "appliedChangeCount": len(preview["changes"]),
        "reviewedUnitCount": preview["reviewedUnitCount"],
        "selectedPo": preview["selectedPo"],
        "files": {
            relative: {"beforeSha256": before[relative], "afterSha256": after[relative]}
            for relative in sorted(after)
        },
    }


def record_review_completion(
    selected_po: list[str],
    modified_ids: set[str],
    notes: dict[str, Any],
    *,
    reviewed_at: str,
) -> dict[str, Any]:
    mapping = _validate_existing_project(validate_repository_inputs())
    reviewed_ids = {
        unit["id"] for unit in mapping["units"] if unit["po"] in set(selected_po)
    }
    existing = load_json(REVIEW_LEDGER_PATH) if REVIEW_LEDGER_PATH.exists() else None
    ledger = record_reviewed_units(
        _current_units(mapping),
        existing,
        reviewed_ids=reviewed_ids,
        modified_ids=modified_ids,
        reviewed_at=reviewed_at,
        release_id=mapping["releaseId"],
        translation_revision=mapping["translationRevision"],
    )
    ledger_errors = validate_schema(ledger, ROOT / "schema/review-ledger.schema.json")
    note_errors = validate_schema(notes, ROOT / "schema/translation-notes.schema.json")
    if ledger_errors or note_errors:
        raise ValueError("review state schema failed: " + "; ".join(ledger_errors + note_errors))
    _write_json(TRANSLATION_NOTES_PATH, notes)
    _write_json(REVIEW_LEDGER_PATH, ledger)
    return ledger


def validate_current_state() -> dict[str, Any]:
    manifest, documents, display = full_inputs()
    ledger = load_json(REVIEW_LEDGER_PATH) if REVIEW_LEDGER_PATH.exists() else None
    candidate = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=[item[1] for item in documents],
        display_values=display,
        migration_report=_migration_gate_stub(),
        review_ledger=ledger,
    )
    release = validate_release(
        profile="release",
        manifest=manifest,
        documents=[item[1] for item in documents],
        display_values=display,
        migration_report=_migration_gate_stub(),
        review_ledger=ledger,
    )
    if not candidate["valid"]:
        raise ValueError("candidate validation failed")
    if release["valid"]:
        raise ValueError("nonpublishable P4.5 source unexpectedly passed release validation")
    return {"candidate": candidate, "release": release}


def build_current_candidate(validation: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest, documents, display = full_inputs()
    validation = validation or validate_current_state()
    with tempfile.TemporaryDirectory(prefix="ipg-current-candidate-") as temporary:
        temporary_root = Path(temporary)
        first = temporary_root / "first"
        second = temporary_root / "second"
        build_outputs(
            first,
            manifest,
            [item[1] for item in documents],
            display,
            candidate=True,
            profile="candidate",
            source_root=ROOT,
        )
        build_outputs(
            second,
            manifest,
            [item[1] for item in documents],
            display,
            candidate=True,
            profile="candidate",
            source_root=ROOT,
        )
        names = ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json")
        if any((first / name).read_bytes() != (second / name).read_bytes() for name in names):
            raise ValueError("current candidate build is not byte deterministic")
        output_errors = validate_schema(
            load_json(first / "rules.json"), ROOT / "schema/ipg-output.schema.json"
        )
        if output_errors:
            raise ValueError("candidate output schema failed: " + "; ".join(output_errors))
        if CURRENT_CANDIDATE.exists():
            shutil.rmtree(CURRENT_CANDIDATE)
        CURRENT_CANDIDATE.mkdir(parents=True)
        for name in names:
            shutil.copyfile(first / name, CURRENT_CANDIDATE / name)
    report = {
        "schemaVersion": 1,
        **validation,
        "byteIdentical": True,
        "files": {
            name: sha256_bytes((CURRENT_CANDIDATE / name).read_bytes()) for name in names
        },
    }
    _write_json(CURRENT_CANDIDATE / "validation.json", report)
    return report


def rollback_applied_writeback(
    formal_files: dict[str, bytes], mapping_bytes: bytes
) -> None:
    for relative, content in formal_files.items():
        (ROOT / relative).write_bytes(content)
    MAPPING_PATH.write_bytes(mapping_bytes)
