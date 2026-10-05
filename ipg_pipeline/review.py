from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from .core import sha256_text


def extract_translation_notes(
    tmx_path: Path,
    units: list[dict[str, Any]],
    *,
    release_id: str,
    translation_revision: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    known = {unit["id"]: unit for unit in units}
    notes: list[dict[str, Any]] = []
    root = ET.parse(tmx_path).getroot()
    for translation_unit in root.findall(".//tu"):
        unit_id = translation_unit.attrib.get("tuid", "")
        props = {
            prop.attrib.get("type", ""): prop.text or ""
            for prop in translation_unit.findall("prop")
        }
        note = props.get("x-translation-note", "").strip()
        if not note:
            continue
        source = ""
        target = ""
        for variant in translation_unit.findall("tuv"):
            language = variant.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            segment = variant.find("seg")
            value = "" if segment is None else "".join(segment.itertext())
            if language.startswith("en"):
                source = value
            elif language.startswith("zh"):
                target = value
        timestamp = props.get("x-recorded-at", "").strip() or recorded_at
        if not timestamp:
            raise ValueError(
                f"TMX note {unit_id!r} has no x-recorded-at; provide recorded_at explicitly"
            )
        source_hash = sha256_text(source)
        target_hash = sha256_text(target)
        current = known.get(unit_id)
        notes.append(
            {
                "unitId": unit_id,
                "sourceHash": source_hash,
                "targetHash": target_hash,
                "releaseId": release_id,
                "translationRevision": translation_revision,
                "note": note,
                "recordedAt": timestamp,
                "stale": current is None
                or source_hash != sha256_text(current["source"])
                or target_hash != sha256_text(current["target"]),
            }
        )
    return {
        "schemaVersion": 1,
        "releaseId": release_id,
        "translationRevision": translation_revision,
        "notes": notes,
    }


def extract_omegat_notes(
    tmx_path: Path,
    units: list[dict[str, Any]],
    *,
    release_id: str,
    translation_revision: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Extract real OmegaT <note> elements and resolve them fail-closed."""
    known = {unit["id"]: unit for unit in units}
    by_pair: dict[tuple[str, str], list[str]] = {}
    for unit in units:
        by_pair.setdefault((unit["source"], unit["target"]), []).append(unit["id"])
    resolved: dict[str, dict[str, Any]] = {}
    root = ET.parse(tmx_path).getroot()
    for translation_unit in root.findall(".//tu"):
        direct_note = translation_unit.find("note")
        props = {
            prop.attrib.get("type", ""): prop.text or ""
            for prop in translation_unit.findall("prop")
        }
        note = (
            "" if direct_note is None else "".join(direct_note.itertext())
        ).strip() or props.get("x-translation-note", "").strip()
        if not note:
            continue
        source = ""
        target = ""
        for variant in translation_unit.findall("tuv"):
            language = variant.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            segment = variant.find("seg")
            value = "" if segment is None else "".join(segment.itertext())
            if language.casefold().startswith("en"):
                source = value
            elif language.casefold().startswith("zh"):
                target = value
        identity_candidates = [translation_unit.attrib.get("tuid", "")]
        identity_candidates.extend(
            props.get(name, "") for name in ("x-unit-id", "unit-id", "id", "tuid")
        )
        explicit = {candidate for candidate in identity_candidates if candidate in known}
        if len(explicit) > 1:
            raise ValueError(f"conflicting stable identities for OmegaT note: {sorted(explicit)}")
        if explicit:
            unit_id = next(iter(explicit))
        else:
            candidates = by_pair.get((source, target), [])
            if not candidates:
                raise ValueError("orphan OmegaT note: source/target pair matches no current unit")
            if len(candidates) > 1:
                raise ValueError(
                    f"ambiguous OmegaT note source/target pair: {sorted(candidates)}"
                )
            unit_id = candidates[0]
        previous = resolved.get(unit_id)
        if previous and previous["note"] != note:
            raise ValueError(f"conflicting OmegaT notes for {unit_id}")
        timestamp = (
            props.get("x-recorded-at", "").strip()
            or translation_unit.attrib.get("changedate", "").strip()
            or translation_unit.attrib.get("creationdate", "").strip()
            or recorded_at
        )
        if not timestamp:
            raise ValueError(f"OmegaT note for {unit_id} has no time; provide recorded_at")
        current = known[unit_id]
        source_hash = sha256_text(source)
        target_hash = sha256_text(target)
        resolved[unit_id] = {
            "unitId": unit_id,
            "sourceHash": source_hash,
            "targetHash": target_hash,
            "releaseId": release_id,
            "translationRevision": translation_revision,
            "note": note,
            "recordedAt": timestamp,
            "stale": source_hash != sha256_text(current["source"])
            or target_hash != sha256_text(current["target"]),
        }
    return {
        "schemaVersion": 1,
        "releaseId": release_id,
        "translationRevision": translation_revision,
        "notes": [resolved[unit_id] for unit_id in sorted(resolved)],
    }


def build_review_ledger(
    units: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    *,
    release_id: str | None = None,
    translation_revision: str | None = None,
) -> dict[str, Any]:
    actions_by_id = {action["unitId"]: action for action in actions}
    entries: list[dict[str, Any]] = []
    for unit in units:
        source_hash = sha256_text(unit["source"])
        target_hash = sha256_text(unit["target"])
        action = actions_by_id.get(unit["id"])
        if action is None:
            status = "unreviewed"
            reviewed_at = None
        elif action["sourceHash"] != source_hash or action["targetHash"] != target_hash:
            status = "stale"
            reviewed_at = action.get("reviewedAt")
        else:
            status = "reviewed-modified" if action.get("modified") else "reviewed-unchanged"
            reviewed_at = action.get("reviewedAt")
        entries.append(
            {
                "unitId": unit["id"],
                "sourceHash": source_hash,
                "targetHash": target_hash,
                "status": status,
                "reviewedAt": reviewed_at,
            }
        )
    ledger = {"schemaVersion": 1, "entries": entries}
    if release_id is not None:
        ledger["releaseId"] = release_id
    if translation_revision is not None:
        ledger["translationRevision"] = translation_revision
    return ledger


def refresh_review_ledger(
    units: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    *,
    release_id: str,
    translation_revision: str,
) -> dict[str, Any]:
    previous = {
        entry["unitId"]: entry for entry in (existing or {}).get("entries", [])
    }
    entries = []
    for unit in units:
        source_hash = sha256_text(unit["source"])
        target_hash = sha256_text(unit["target"])
        old = previous.get(unit["id"])
        if old is None or old.get("status") == "unreviewed":
            status, reviewed_at = "unreviewed", None
        elif old.get("sourceHash") != source_hash or old.get("targetHash") != target_hash:
            status, reviewed_at = "stale", old.get("reviewedAt")
        else:
            status, reviewed_at = old["status"], old.get("reviewedAt")
        entries.append(
            {
                "unitId": unit["id"],
                "sourceHash": source_hash,
                "targetHash": target_hash,
                "status": status,
                "reviewedAt": reviewed_at,
            }
        )
    return {
        "schemaVersion": 1,
        "releaseId": release_id,
        "translationRevision": translation_revision,
        "entries": entries,
    }


def record_reviewed_units(
    units: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    *,
    reviewed_ids: set[str],
    modified_ids: set[str],
    reviewed_at: str,
    release_id: str,
    translation_revision: str,
) -> dict[str, Any]:
    ledger = refresh_review_ledger(
        units,
        existing,
        release_id=release_id,
        translation_revision=translation_revision,
    )
    for entry in ledger["entries"]:
        if entry["unitId"] not in reviewed_ids:
            continue
        entry["status"] = (
            "reviewed-modified"
            if entry["unitId"] in modified_ids
            else "reviewed-unchanged"
        )
        entry["reviewedAt"] = reviewed_at
    return ledger


def review_status_report(ledger: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(entry["status"] for entry in ledger["entries"])
    return {
        "schemaVersion": 1,
        "total": len(ledger["entries"]),
        "counts": {
            status: counts.get(status, 0)
            for status in ("unreviewed", "reviewed-unchanged", "reviewed-modified", "stale")
        },
    }


def terminology_audit(glossary_path: Path, units: list[dict[str, Any]]) -> dict[str, Any]:
    glossary: list[tuple[str, str, int]] = []
    issues: list[dict[str, Any]] = []
    for line_number, raw in enumerate(glossary_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
            issues.append({"code": "invalid-glossary-row", "line": line_number})
            continue
        glossary.append((parts[0].strip(), parts[1].strip(), line_number))
    findings: list[dict[str, Any]] = []
    for source_term, target_term, line_number in glossary:
        for unit in units:
            if source_term in unit["source"] and target_term not in unit["target"]:
                findings.append(
                    {
                        "code": "term-target-missing",
                        "unitId": unit["id"],
                        "sourceTerm": source_term,
                        "expectedTarget": target_term,
                        "glossaryLine": line_number,
                    }
                )
    return {
        "schemaVersion": 1,
        "nonBlocking": True,
        "glossaryIssueCount": len(issues),
        "findingCount": len(findings),
        "glossaryIssues": issues,
        "findings": findings,
    }
