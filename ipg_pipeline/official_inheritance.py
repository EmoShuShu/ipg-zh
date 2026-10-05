"""Native IPG candidate inheritance; never writes current source or review stores."""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from typing import Any

from .core import sha256_text
from .omegat import collect_units
from .reconcile import official_records
from .review import refresh_review_ledger, review_status_report


def structured_diff(previous: list[dict], current: dict) -> dict:
    old = {r["node"]["id"]: r for r in official_records(previous)}
    new = {r["node"]["id"]: r for r in official_records([current])}
    common = old.keys() & new.keys()
    reordered = set()
    for parent in {old[i]["parent"] for i in common}:
        ids = [i for i in common if old[i]["parent"] == new[i]["parent"] == parent]
        before = sorted(ids, key=lambda i: old[i]["order"])
        after = sorted(ids, key=lambda i: new[i]["order"])
        reordered.update(i for a, b in zip(before, after) if a != b for i in (a, b))
    rows = []
    for i in sorted(common):
        changes = {}
        for field in ("number", "kind", "role", "labelCode", "date", "type", "marker", "penaltyCode", "displayCode", "referenceId", "ruleReferences"):
            a, b = old[i]["node"].get(field), new[i]["node"].get(field)
            if a != b:
                changes[field] = {"from": a, "to": b}
        field = "title" if old[i]["kind"] == "section" else "text" if old[i]["kind"] == "block" else None
        if field and old[i]["node"][field]["en"] != new[i]["node"][field]["en"]:
            changes["english"] = {"from": old[i]["node"][field]["en"], "to": new[i]["node"][field]["en"]}
        if old[i]["parent"] != new[i]["parent"]:
            changes["parent"] = {"from": old[i]["parent"], "to": new[i]["parent"]}
        if i in reordered:
            changes["order"] = {"from": old[i]["order"], "to": new[i]["order"]}
        if changes:
            rows.append({"id": i, "kind": old[i]["kind"], "changes": changes})
    added = [{"id": i, "kind": new[i]["kind"], "english": new[i]["node"].get("text", new[i]["node"].get("title", {})).get("en", "")} for i in sorted(new.keys() - old.keys())]
    deleted = [{"id": i, "kind": old[i]["kind"], "english": old[i]["node"].get("text", old[i]["node"].get("title", {})).get("en", "")} for i in sorted(old.keys() - new.keys())]
    return {"added": added, "deleted": deleted, "changed": rows,
        "summary": {"added": len(added), "deleted": len(deleted), "changed": len(rows),
            "englishChanged": sum("english" in r["changes"] for r in rows),
            "renumbered": sum("number" in r["changes"] for r in rows),
            "moved": sum("parent" in r["changes"] for r in rows),
            "reordered": len(reordered)}}


def inherit_editorial_state(project: dict, reconciled: dict, manifest: dict,
                            notes: dict | None = None) -> dict[str, Any]:
    document = reconciled["document"]
    previous = [d for _, d in project["documents"]]
    diff = structured_diff(previous, document)
    records = official_records([document])
    known = {r["node"]["id"]: r for r in records}
    segments = {r["node"]["id"]: {s["id"] for s in r["node"].get("readingSegments", [])} for r in records}
    changed = {r["id"] for r in diff["changed"] if set(r["changes"]) - {"order", "number"}}
    context_changed = {r["id"] for r in diff["changed"] if set(r["changes"]) - {"order", "number", "english"}}
    findings = copy.deepcopy(reconciled["reconciliation"]["findings"])
    unresolved, inherited, stale_annotations = [], [], set()
    for annotation in [copy.deepcopy(a) for d in previous for a in d["publicationAnnotations"]]:
        anchor = annotation["anchor"]
        reason = None
        if anchor["id"] not in known or known[anchor["id"]]["kind"] != anchor["type"]:
            reason = "publication-annotation-anchor-lost"
        elif anchor.get("segmentId") and anchor["segmentId"] not in segments.get(anchor["id"], set()):
            reason = "publication-annotation-segment-lost"
        elif any(i not in known for i in annotation.get("appliesTo", [])):
            reason = "publication-annotation-applies-to-lost"
        if reason:
            unresolved.append({"reason": reason, "annotation": annotation})
            findings.append({"code": reason, "annotationId": annotation["id"], "anchor": anchor})
            continue
        inherited.append(annotation)
        if anchor["id"] in changed or set(annotation.get("appliesTo", [])) & changed:
            stale_annotations.update("annotation:" + b["id"] for g in annotation["groups"] for b in g["blocks"])
            findings.append({"code": "publication-annotation-context-changed", "annotationId": annotation["id"]})
    document["publicationAnnotations"] = inherited
    # Keep chapter-oriented source files, without borrowing MTR's document organization.
    parts = defaultdict(lambda: {"schemaVersion": 1, "documentId": "", "sections": [], "publicationAnnotations": []})
    owners = {}
    for section in document["sections"]:
        number = section["number"]
        name = "front-matter" if section["kind"] == "front-matter" else "appendix-" + number.lower() if section["kind"] == "appendix" else f"chapter-{int(number.split('.')[0]):02d}"
        parts[name]["documentId"] = "ipg-" + name
        parts[name]["sections"].append(section)
        owners[section["id"]] = name
    for annotation in inherited:
        section = known[annotation["anchor"]["id"]]["section"]
        parts[owners[section]]["publicationAnnotations"].append(annotation)
    documents = [(name + ".yaml", doc) for name, doc in parts.items()]
    manifest["documents"] = [name for name, _ in documents]
    manifest["scope"].setdefault("officialContent", {})["included"] = [s["number"] for s in document["sections"]]
    scope = manifest["scope"]["publicationAnnotations"]
    scope["includedSections"] = [s["number"] for s in document["sections"]]
    scope["deferredGroups"] = len(unresolved)
    scope["deferredRawUnits"] = len({i for a in unresolved for g in a["annotation"]["groups"] for b in g["blocks"] for i in b.get("sourceRawUnits", [])})
    units = collect_units(documents, project["display"], release_relative="candidate/source")
    stale_context_units = set()
    for record in records:
        ancestry, cursor = {record["node"]["id"]}, record["parent"]
        while cursor is not None:
            ancestry.add(cursor)
            cursor = known[cursor]["parent"]
        if ancestry & context_changed:
            node = record["node"]
            stale_context_units.add(("title:" if record["kind"] == "section" else "block:") + node["id"])
            stale_context_units.update("reading-segment:" + s["id"] for s in node.get("readingSegments", []))
    revision, release_id = manifest["versions"]["translation"]["revision"], manifest["releaseId"]
    prior_ledger = project["ledger"]
    if prior_ledger and len({e["unitId"] for e in prior_ledger["entries"]}) != len(prior_ledger["entries"]):
        raise ValueError("duplicate base review records")
    ledger = refresh_review_ledger(units, prior_ledger, release_id=release_id, translation_revision=revision)
    prior_entries = {e["unitId"]: e for e in (prior_ledger or {}).get("entries", [])}
    # Review context can change without changing an annotation's own English.
    for entry in ledger["entries"]:
        old = prior_entries.get(entry["unitId"])
        if entry["unitId"] in stale_annotations | stale_context_units or old and old["sourceHash"] != entry["sourceHash"]:
            entry["status"] = "stale"
    note_store = copy.deepcopy(notes or {"schemaVersion": 1, "notes": []})
    note_store.update(releaseId=release_id, translationRevision=revision)
    by_unit = {u["id"]: u for u in units}
    for note in note_store["notes"]:
        unit = by_unit.get(note["unitId"])
        note.update(releaseId=release_id, translationRevision=revision)
        note["stale"] = bool(note["stale"] or unit is None or note["sourceHash"] != sha256_text(unit["source"])
                             or note["targetHash"] != sha256_text(unit["target"])
                             or note["unitId"] in stale_annotations | stale_context_units)
        if unit is None:
            findings.append({"code": "translation-note-unit-deleted", "unitId": note["unitId"]})
    current_segments = {s for values in segments.values() for s in values}
    prior_segments = {s["id"] for r in official_records(previous) for s in r["node"].get("readingSegments", [])}
    for entry in reconciled["registry"]["entries"]:
        if entry["id"] in prior_segments - current_segments and entry["status"] == "active":
            entry["status"] = "retired"
            entry.setdefault("history", []).append({"event": "retired", "atVersion": release_id, "reason": "reading division invalidated by official change"})
    ids = set(by_unit)
    deleted_reviews = [e for e in (prior_ledger or {}).get("entries", []) if e["unitId"] not in ids]
    prior_ids = {r["node"]["id"] for r in official_records(previous)}
    counts = {"officialIdsPreserved": len(prior_ids & set(known)), "officialIdsNew": len(set(known) - prior_ids),
        "officialIdsRetired": len(reconciled["reconciliation"]["retiredIds"]),
        "translationsInherited": sum(bool(u["target"].strip()) for u in units),
        "missingTranslations": sum(not u["target"].strip() for u in units),
        "annotationsInherited": len(inherited), "annotationsUnresolved": len(unresolved),
        "notesInherited": len(note_store["notes"]), "notesStale": sum(n["stale"] for n in note_store["notes"]),
        "review": review_status_report(ledger)["counts"], "deletedReviewRecordsArchived": len(deleted_reviews)}
    return {"manifest": manifest, "documents": documents, "registry": reconciled["registry"],
        "ledger": ledger, "notes": note_store, "unresolvedAnnotations": unresolved,
        "deletedReviewRecords": deleted_reviews, "diff": diff, "findings": findings, "inheritance": counts}
