from __future__ import annotations

import json
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
from .p4 import DOCUMENTS, RELEASE_DIR
from .review import terminology_audit


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
