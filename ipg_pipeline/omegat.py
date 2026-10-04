from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from .core import sha256_bytes, sha256_text


def collect_units(
    documents: list[tuple[str, dict[str, Any]]], display_values: dict[str, Any]
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for code, value in sorted(display_values["values"].items()):
        units.append(
            {
                "id": f"display:{code}",
                "kind": "display-value",
                "source": value["en"],
                "target": value["zh"],
                "file": "src/ipg/display-values.yaml",
                "pointer": ["values", code, "zh"],
            }
        )
    for filename, document in documents:
        relative = f"src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001/{filename}"
        for section_index, section in enumerate(document["sections"]):
            units.append(
                {
                    "id": f"title:{section['id']}",
                    "kind": "title",
                    "source": section["title"]["en"],
                    "target": section["title"]["zh"],
                    "file": relative,
                    "pointer": ["sections", section_index, "title", "zh"],
                }
            )
            for component_index, component in enumerate(section["components"]):
                for group_index, group in enumerate(component["groups"]):
                    for block_index, block in enumerate(group["blocks"]):
                        units.append(
                            {
                                "id": f"block:{block['id']}",
                                "kind": "official-body",
                                "source": block["text"]["en"],
                                "target": block["text"]["zh"],
                                "file": relative,
                                "pointer": [
                                    "sections",
                                    section_index,
                                    "components",
                                    component_index,
                                    "groups",
                                    group_index,
                                    "blocks",
                                    block_index,
                                    "text",
                                    "zh",
                                ],
                            }
                        )
        for annotation_index, annotation in enumerate(document["publicationAnnotations"]):
            units.append(
                {
                    "id": f"annotation:{annotation['id']}",
                    "kind": "publication-annotation",
                    "source": annotation["text"]["en"],
                    "target": annotation["text"]["zh"],
                    "file": relative,
                    "pointer": ["publicationAnnotations", annotation_index, "text", "zh"],
                }
            )
    ids = [unit["id"] for unit in units]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate OmegaT unit id generated")
    return units


def _po_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_po(units: list[dict[str, Any]]) -> str:
    lines = [
        'msgid ""',
        'msgstr "Content-Type: text/plain; charset=UTF-8\\n"',
        "",
    ]
    for unit in units:
        lines.extend(
            [
                f"#. kind: {unit['kind']}",
                f"#. source-sha256: {sha256_text(unit['source'])}",
                f"msgctxt {_po_quote(unit['id'])}",
                f"msgid {_po_quote(unit['source'])}",
                f"msgstr {_po_quote(unit['target'])}",
                "",
            ]
        )
    return "\n".join(lines)


def parse_po(path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    active_field: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines() + [""]:
        line = raw_line.strip()
        if not line:
            if "id" in current:
                entries.append(current)
            current = {}
            active_field = None
            continue
        if line.startswith("msgctxt "):
            active_field = "id"
            current[active_field] = json.loads(line[8:])
        elif line.startswith("msgid "):
            active_field = "source"
            current[active_field] = json.loads(line[6:])
        elif line.startswith("msgstr "):
            active_field = "target"
            current[active_field] = json.loads(line[7:])
        elif line.startswith('"') and active_field:
            current[active_field] += json.loads(line)
    return entries


def export_project(
    project_dir: Path,
    source_root: Path,
    documents: list[tuple[str, dict[str, Any]]],
    display_values: dict[str, Any],
    glossary_path: Path,
) -> dict[str, Any]:
    units = collect_units(documents, display_values)
    for name in ("source", "target", "tm", "glossary", "omegat", "dictionary"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)
    po = render_po(units)
    (project_dir / "source/ipg-pilot.po").write_text(po, encoding="utf-8", newline="\n")
    (project_dir / "target/ipg-pilot.po").write_text(po, encoding="utf-8", newline="\n")
    shutil.copyfile(glossary_path, project_dir / "glossary/ipg-glossary.txt")
    project_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<omegat><project version=\"1.0\"><sourceDir>source/</sourceDir>"
        "<targetDir>target/</targetDir><tmDir>tm/</tmDir>"
        "<glossaryDir>glossary/</glossaryDir></project></omegat>\n"
    )
    (project_dir / "omegat.project").write_text(project_xml, encoding="utf-8", newline="\n")
    source_files = sorted({unit["file"] for unit in units})
    mapping = {
        "schemaVersion": 1,
        "units": [
            {
                **unit,
                "sourceHash": sha256_text(unit["source"]),
                "targetHash": sha256_text(unit["target"]),
            }
            for unit in units
        ],
        "sourceFiles": {
            relative: sha256_bytes((source_root / relative).read_bytes()) for relative in source_files
        },
    }
    (project_dir / "omegat/ipg-pilot.mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return mapping


def validate_target_entries(
    mapping: dict[str, Any], entries: list[dict[str, str]]
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    expected = {unit["id"]: unit for unit in mapping["units"]}
    seen: set[str] = set()
    for entry in entries:
        unit_id = entry["id"]
        if unit_id in seen:
            errors.append({"code": "duplicate-id", "unitId": unit_id})
            continue
        seen.add(unit_id)
        if unit_id not in expected:
            errors.append({"code": "unknown-id", "unitId": unit_id})
            continue
        if sha256_text(entry["source"]) != expected[unit_id]["sourceHash"]:
            errors.append({"code": "source-changed", "unitId": unit_id})
    for unit_id in sorted(set(expected) - seen):
        errors.append({"code": "missing-id", "unitId": unit_id})
    return errors


def _yaml_node_at_pointer(root: yaml.Node, pointer: list[Any]) -> yaml.Node:
    node = root
    for part in pointer:
        if isinstance(node, yaml.MappingNode):
            match = next(
                (value for key, value in node.value if key.value == str(part)),
                None,
            )
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            match = node.value[part] if 0 <= part < len(node.value) else None
        else:
            match = None
        if match is None:
            raise KeyError(f"YAML pointer not found: {pointer}")
        node = match
    return node


def minimal_yaml_update(path: Path, pointer: list[Any], expected: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    root = yaml.compose(text)
    if root is None:
        raise ValueError(f"empty YAML file: {path}")
    node = _yaml_node_at_pointer(root, pointer)
    if not isinstance(node, yaml.ScalarNode) or node.value != expected:
        raise ValueError(f"YAML value changed before writeback: {pointer}")
    encoded = json.dumps(replacement, ensure_ascii=False)
    updated = text[: node.start_mark.index] + encoded + text[node.end_mark.index :]
    path.write_text(updated, encoding="utf-8", newline="")


def preview_writeback(
    project_dir: Path, source_root: Path, candidate_root: Path
) -> dict[str, Any]:
    mapping = json.loads((project_dir / "omegat/ipg-pilot.mapping.json").read_text(encoding="utf-8"))
    target_po = project_dir / "target/ipg-pilot.po"
    entries = parse_po(target_po)
    errors = validate_target_entries(mapping, entries)
    for relative, expected_hash in mapping["sourceFiles"].items():
        actual = sha256_bytes((source_root / relative).read_bytes())
        if actual != expected_hash:
            errors.append({"code": "file-changed-since-export", "file": relative})
    if errors:
        raise ValueError(json.dumps(errors, ensure_ascii=False, sort_keys=True))

    if candidate_root.exists():
        resolved = candidate_root.resolve()
        if source_root.resolve() not in resolved.parents:
            raise ValueError("candidate directory must stay inside the project")
        shutil.rmtree(candidate_root)
    candidate_root.mkdir(parents=True)
    by_id = {entry["id"]: entry for entry in entries}
    changes = []
    for unit in mapping["units"]:
        target = by_id[unit["id"]]["target"]
        if target == unit["target"]:
            continue
        relative = unit["file"]
        candidate_file = candidate_root / relative
        if not candidate_file.exists():
            candidate_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / relative, candidate_file)
        minimal_yaml_update(candidate_file, unit["pointer"], unit["target"], target)
        changes.append(
            {
                "unitId": unit["id"],
                "file": relative,
                "pointer": unit["pointer"],
                "oldTargetHash": sha256_text(unit["target"]),
                "newTargetHash": sha256_text(target),
            }
        )
    changed_files = sorted({item["file"] for item in changes})
    preview = {
        "schemaVersion": 1,
        "targetPoSha256": sha256_bytes(target_po.read_bytes()),
        "expectedChangeCount": len(changes),
        "baseFiles": {name: mapping["sourceFiles"][name] for name in changed_files},
        "candidateFiles": {
            name: sha256_bytes((candidate_root / name).read_bytes()) for name in changed_files
        },
        "changes": changes,
    }
    return preview


def apply_writeback(
    project_dir: Path,
    source_root: Path,
    candidate_root: Path,
    preview: dict[str, Any],
    *,
    expected_change_count: int,
) -> None:
    if preview["expectedChangeCount"] != expected_change_count:
        raise ValueError("expected change count does not match preview")
    target_hash = sha256_bytes((project_dir / "target/ipg-pilot.po").read_bytes())
    if target_hash != preview["targetPoSha256"]:
        raise ValueError("target PO changed after preview")
    for relative, expected_hash in preview["baseFiles"].items():
        if sha256_bytes((source_root / relative).read_bytes()) != expected_hash:
            raise ValueError(f"source file changed after preview: {relative}")
        candidate_file = candidate_root / relative
        if sha256_bytes(candidate_file.read_bytes()) != preview["candidateFiles"][relative]:
            raise ValueError(f"candidate file changed after preview: {relative}")
    for relative in sorted(preview["candidateFiles"]):
        shutil.copyfile(candidate_root / relative, source_root / relative)
