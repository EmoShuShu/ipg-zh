from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .builder import build_outputs
from .core import (
    ROOT,
    load_json,
    load_yaml,
    sha256_bytes,
    sha256_text,
    validate_registry,
    validate_schema,
)
from .omegat import collect_units, parse_po, render_po, validate_target_entries
from .omegat import minimal_yaml_update
from .p4 import DOCUMENTS, RELEASE_DIR
from .review import terminology_audit
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
    "chapter-01.po": 109,
    "chapter-02.po": 273,
    "chapter-03.po": 252,
    "chapter-04.po": 207,
    "appendix-a.po": 24,
    "appendix-b.po": 18,
}
EXPECTED_UNIT_COUNT = 905


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, content: Any) -> None:
    _write_text(path, json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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
    <sourceDir>source/</sourceDir>
    <targetDir>target/</targetDir>
    <tmDir>tm/</tmDir>
    <glossaryDir>../../terminology/</glossaryDir>
    <glossaryFile>ipg-glossary.txt</glossaryFile>
    <dictionaryDir>dictionary/</dictionaryDir>
    <sourceLang>EN-US</sourceLang>
    <targetLang>ZH-CN</targetLang>
    <sourceTok>org.omegat.tokenizer.LuceneEnglishTokenizer</sourceTok>
    <targetTok>org.omegat.tokenizer.LuceneSmartChineseTokenizer</targetTok>
    <sentenceSeg>false</sentenceSeg>
    <supportDefaultTranslations>false</supportDefaultTranslations>
    <removeTags>false</removeTags>
  </project>
</omegat>
"""


def _filters_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<filters>
  <filter className="org.omegat.filters2.po.PoFilter2">
    <files targetEncoding="UTF-8" sourceEncoding="UTF-8" targetFilenamePattern="${filename}" sourceFilenameMask="*.po" />
    <option name="skipHeader" value="false" />
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
    errors = validate_schema(manifest, ROOT / "schema/ipg-manifest.schema.json")
    for filename, document in documents:
        errors.extend(
            f"{filename}: {item}"
            for item in validate_schema(document, ROOT / "schema/ipg-source.schema.json")
        )
    errors.extend(validate_registry(load_yaml(ROOT / "src/ipg/id-registry.yaml")))
    if errors:
        raise ValueError("repository validation failed: " + "; ".join(errors))
    grouped = units_by_po(documents, display)
    units = [unit for name in PO_ORDER for unit in grouped[name]]
    with tempfile.TemporaryDirectory(prefix="ipg-review-output-") as temporary:
        output = Path(temporary)
        build_outputs(output, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate")
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
    for filename, document in documents:
        errors = validate_schema(document, ROOT / "schema/ipg-source.schema.json")
        if errors:
            raise ValueError(f"candidate schema failed for {filename}: {'; '.join(errors)}")
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
    build_outputs(first, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate")
    build_outputs(second, manifest, [item[1] for item in documents], display, candidate=True, profile="candidate")
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


def preview_full_writeback(selected_po: list[str], candidate_root: Path) -> dict[str, Any]:
    inspection = inspect_target_project()
    mapping = inspection["mapping"]
    entries = inspection["entries"]
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
        actual = _leaf_differences(load_yaml(ROOT / relative), load_yaml(candidate_root / relative))
        if actual != allowed:
            raise ValueError(f"candidate changed fields outside selected zh scalars: {relative}")
    validation = _validate_candidate_overlay(candidate_root)
    target_hashes = {
        name: sha256_bytes((PROJECT_DIR / "target" / name).read_bytes()) for name in PO_ORDER
    }
    selected_ids = {
        unit["id"] for unit in mapping["units"] if unit["po"] in selected
    }
    missing_remaining = sum(not target[unit_id]["target"] for unit_id in mapping["initialMissingUnitIds"])
    return {
        "schemaVersion": 1,
        "selectedPo": selected,
        "reviewedUnitCount": len(selected_ids),
        "actualChangeCount": len(changes),
        "reviewedUnchangedCount": len(selected_ids) - len(changes),
        "initialMissingRemaining": missing_remaining,
        "changes": changes,
        "mappingSha256": sha256_bytes(MAPPING_PATH.read_bytes()),
        "targetPoHashes": target_hashes,
        "tmxSha256": _tmx_hash(),
        "baseFiles": mapping["sourceFiles"],
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
