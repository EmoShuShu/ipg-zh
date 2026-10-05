"""Explicit fail-closed upgrade of an existing OmegaT project reading layout."""
from __future__ import annotations

import copy
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from . import full_review as workflow
from .core import load_json, sha256_bytes, sha256_text, walk_nodes
from .omegat import parse_po, render_po, validate_target_entries
from .review import refresh_review_ledger


def upgrade_project() -> dict:
    """User must save and close OmegaT. Preserve the entire original directory."""
    project = workflow.PROJECT_DIR
    backup = project.with_name(project.name + ".before-reading-layout")
    staged = project.with_name(project.name + ".reading-layout-stage")
    if backup.exists() or staged.exists():
        raise ValueError("upgrade backup/staging already exists; do not overwrite review evidence")
    inputs = workflow.validate_repository_inputs()
    old = load_json(workflow.MAPPING_PATH)
    new = workflow._new_mapping(inputs)
    current = {unit["id"]: unit for unit in new["units"]}
    parents = {f"block:{block['id']}": block for _, d in inputs["documents"] for kind, block in walk_nodes([d]) if kind == "block" and "readingSegments" in block}
    replaced = set(unit["id"] for unit in old["units"]) - set(current)
    if not replaced <= set(parents):
        raise ValueError("old unit identity cannot be reconciled with the reading layout")
    for unit in old["units"]:
        content = current.get(unit["id"])
        text = parents[unit["id"]]["text"] if content is None else {"en": content["source"], "zh": content["target"]}
        if unit["sourceHash"] != sha256_text(text["en"]) or unit["targetHash"] != sha256_text(text["zh"]):
            raise ValueError(f"source/translation changed outside reading upgrade: {unit['id']}")
    for name, expected in old["sourcePoHashes"].items():
        if sha256_bytes((project / "source" / name).read_bytes()) != expected:
            raise ValueError(f"old source PO changed: {name}")
    target_files = list((project / "target").glob("*.po"))
    if target_files and {p.name for p in target_files} != set(workflow.PO_ORDER):
        raise ValueError("old target PO set is incomplete; compile all files or preserve for manual recovery")
    targets = {}
    for path in target_files:
        entries = parse_po(path)
        errors = validate_target_entries({"units": [u for u in old["units"] if u["po"] == path.name]}, entries)
        if errors:
            raise ValueError(errors)
        targets.update({entry["id"]: entry["target"] for entry in entries})
    for unit in old["units"]:
        if unit["id"] in replaced and targets.get(unit["id"], unit["target"]) != unit["target"]:
            raise ValueError(f"edited parent translation needs manual splitting: {unit['id']}")

    tmx_path = project / "omegat/project_save.tmx"
    tmx = ET.parse(tmx_path) if tmx_path.exists() else None
    converted_tu_count = 0
    if tmx is not None:
        body = tmx.getroot().find("body")
        for tu in list(body):
            props = {p.get("type"): p.text or "" for p in tu.findall("prop")}
            unit_id = props.get("path", tu.get("tuid", ""))
            if unit_id not in replaced:
                continue
            variants = {v.get("lang", v.get("{http://www.w3.org/XML/1998/namespace}lang", "")): v for v in tu.findall("tuv")}
            en = next((v for lang, v in variants.items() if lang.lower().startswith("en")), None)
            zh = next((v for lang, v in variants.items() if lang.lower().startswith("zh")), None)
            parent = parents[unit_id]
            if en is None or zh is None or "".join(en.find("seg").itertext()) != parent["text"]["en"] or "".join(zh.find("seg").itertext()) != parent["text"]["zh"]:
                raise ValueError(f"edited parent TMX cannot be split automatically: {unit_id}")
            if tu.find("note") is not None or props.get("x-translation-note"):
                raise ValueError(f"parent TMX note needs explicit reassignment: {unit_id}")
            index = list(body).index(tu)
            body.remove(tu)
            for offset, segment in enumerate(parent["readingSegments"]):
                child = copy.deepcopy(tu)
                path_prop = child.find("prop[@type='path']")
                if path_prop is None:
                    child.set("tuid", f"reading-segment:{segment['id']}")
                else:
                    path_prop.text = f"reading-segment:{segment['id']}"
                for variant in child.findall("tuv"):
                    lang = variant.get("lang", variant.get("{http://www.w3.org/XML/1998/namespace}lang", ""))
                    value = segment["text"]["en" if lang.lower().startswith("en") else "zh"]
                    seg = variant.find("seg")
                    seg.clear()
                    seg.text = value
                body.insert(index + offset, child)
            converted_tu_count += 1

    # Validate review stores before touching the project; archive superseded records.
    ledger = load_json(workflow.REVIEW_LEDGER_PATH) if workflow.REVIEW_LEDGER_PATH.exists() else None
    notes = load_json(workflow.TRANSLATION_NOTES_PATH) if workflow.TRANSLATION_NOTES_PATH.exists() else None
    old_ids = {unit["id"] for unit in old["units"]}
    ledger_ids = [entry["unitId"] for entry in (ledger or {}).get("entries", [])]
    if ledger is not None and (len(ledger_ids) != len(set(ledger_ids)) or set(ledger_ids) != old_ids):
        raise ValueError("old review ledger is incomplete, duplicate, or contains orphan records")
    if not {n["unitId"] for n in (notes or {}).get("notes", [])} <= old_ids:
        raise ValueError("old translation notes contain orphan identities")
    if any(n["unitId"] in replaced for n in (notes or {}).get("notes", [])):
        raise ValueError("parent translation notes need explicit reassignment")
    updated_ledger = refresh_review_ledger(new["units"], ledger, release_id=new["releaseId"], translation_revision=new["translationRevision"])
    snapshot = {p.relative_to(project).as_posix(): sha256_bytes(p.read_bytes()) for p in project.rglob("*") if p.is_file()}
    shutil.copytree(project, staged)
    for name in workflow.PO_ORDER:
        workflow._write_text(staged / "source" / name, render_po(inputs["grouped"][name]))
        if target_files:
            units = [{**u, "target": targets.get(u["id"], u["target"])} for u in inputs["grouped"][name]]
            workflow._write_text(staged / "target" / name, render_po(units))
    workflow._write_json(staged / "omegat/full-review.mapping.json", new)
    workflow._write_text(staged / "README.md", workflow._project_readme())
    filters = staged / "omegat/filters.xml"
    if filters.exists():
        tree = ET.parse(filters)
        po = tree.getroot().find("filter[@className='org.omegat.filters2.po.PoFilter']")
        if po is not None:
            option = po.find("option[@name='skipHeader']")
            if option is None:
                option = ET.SubElement(po, "option", {"name": "skipHeader"})
            option.set("value", "true")
        tree.write(filters, encoding="UTF-8", xml_declaration=True)
    if tmx is not None:
        tmx.write(staged / "omegat/project_save.tmx", encoding="UTF-8", xml_declaration=True)
    workflow._write_json(staged / "omegat/reading-layout-upgrade.json", {"oldUnitCount": len(old["units"]), "newUnitCount": len(new["units"]), "replacedParentUnits": sorted(replaced), "convertedTmxParents": converted_tu_count, "originalProjectBackup": str(backup)})
    if snapshot != {p.relative_to(project).as_posix(): sha256_bytes(p.read_bytes()) for p in project.rglob("*") if p.is_file()}:
        raise ValueError("OmegaT project changed during upgrade; original left untouched")
    # Windows rejects the move if OmegaT still holds its project lock.
    project.rename(backup)
    try:
        staged.rename(project)
    except Exception:
        backup.rename(project)
        raise
    workflow._write_json(backup / "review-ledger-before-upgrade.json", ledger)
    workflow._write_json(workflow.REVIEW_LEDGER_PATH, updated_ledger)
    return {"backup": str(backup), "unitCount": len(new["units"]), "convertedTmxParents": converted_tu_count}


if __name__ == "__main__":
    print(upgrade_project())
