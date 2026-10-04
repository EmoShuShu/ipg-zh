from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from .core import sha256_text, walk_nodes


def english_evidence(text: str) -> str:
    return sha256_text(" ".join(text.casefold().split()))


def reconcile_block_sequence(
    extracted: list[dict[str, Any]],
    registry: dict[str, Any],
    *,
    context: str,
    version: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Reconcile extraction records without using their array positions as identity."""
    overrides = overrides or {}
    entries = registry.setdefault("entries", [])
    active = [
        entry
        for entry in entries
        if entry.get("status") == "active"
        and entry.get("kind") == "block"
        and entry.get("evidence", {}).get("context") == context
    ]
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in active:
        by_hash[entry.get("evidence", {}).get("englishHash", "")].append(entry)
    used: set[str] = set()
    findings: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    allocated = [
        int(entry["id"].removeprefix("ipg-b"))
        for entry in entries
        if entry.get("id", "").startswith("ipg-b")
        and entry["id"].removeprefix("ipg-b").isdigit()
    ]
    next_number = max(allocated, default=0) + 1

    for item in extracted:
        extraction_id = item["extractionId"]
        forced = overrides.get(extraction_id)
        candidates = (
            [entry for entry in active if entry["id"] == forced]
            if forced
            else by_hash.get(english_evidence(item["text"]), [])
        )
        candidates = [entry for entry in candidates if entry["id"] not in used]
        if len(candidates) > 1:
            findings.append(
                {
                    "code": "ambiguous-identity",
                    "extractionId": extraction_id,
                    "candidateIds": sorted(entry["id"] for entry in candidates),
                }
            )
            resolved.append({**item, "id": None})
            continue
        if candidates:
            stable_id = candidates[0]["id"]
        else:
            stable_id = f"ipg-b{next_number:06d}"
            next_number += 1
            entries.append(
                {
                    "id": stable_id,
                    "kind": "block",
                    "status": "active",
                    "canonicalKey": f"block:{stable_id}",
                    "evidence": {
                        "context": context,
                        "englishHash": english_evidence(item["text"]),
                    },
                    "history": [
                        {"event": "allocated", "atVersion": version, "label": item["text"]}
                    ],
                }
            )
        used.add(stable_id)
        resolved.append({**item, "id": stable_id})

    for entry in active:
        if entry["id"] not in used:
            entry["status"] = "retired"
            entry.setdefault("history", []).append(
                {
                    "event": "retired",
                    "atVersion": version,
                    "reason": "official block absent after reconciliation; id permanently tombstoned",
                }
            )
    return {"items": resolved, "registry": registry, "findings": findings}


def reconcile_section_identity(
    extracted: dict[str, str],
    registry: dict[str, Any],
    *,
    override_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a section rename/renumber without treating title or order as identity."""
    active = [
        entry
        for entry in registry.get("entries", [])
        if entry.get("kind") == "section" and entry.get("status") == "active"
    ]
    if override_id:
        matches = [entry for entry in active if entry["id"] == override_id]
    else:
        matches = [
            entry
            for entry in active
            if entry.get("canonicalKey") == f"section:{extracted['number']}"
        ]
    if len(matches) != 1:
        return {
            "id": None,
            "findings": [
                {
                    "code": "unresolved-section-identity",
                    "extractionId": extracted["extractionId"],
                }
            ],
        }
    return {"id": matches[0]["id"], "findings": []}


def extraction_view(parsed: dict[str, Any]) -> dict[str, Any]:
    """Replace parser-local positional ids with explicitly temporary extraction ids."""
    result = copy.deepcopy(parsed)
    counter = 0
    for document in result["documents"].values():
        for kind, node in walk_nodes([document]):
            counter += 1
            node["id"] = f"extract-{kind}-{counter:04d}"
        document["documentId"] = f"extract-{document['documentId']}"
    result["idStatus"] = "temporary-extraction-ids"
    return result


def reconcile_pilot(
    extracted: dict[str, Any],
    previous_documents: dict[str, dict[str, Any]],
    overrides: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assign prior stable ids by English evidence and structural context."""
    result = copy.deepcopy(extracted)
    findings: list[dict[str, Any]] = []
    identity_overrides = (overrides or {}).get("reconciliation", {}).get(
        "englishHashToStableId", {}
    )
    for filename, document in result["documents"].items():
        previous = previous_documents[filename]
        previous_sections = {section["number"]: section for section in previous["sections"]}
        document["documentId"] = previous["documentId"]
        for section in document["sections"]:
            old_section = previous_sections.get(section["number"])
            if old_section is None:
                findings.append(
                    {"code": "unresolved-section-identity", "extractionId": section["id"]}
                )
                continue
            section["id"] = old_section["id"]
            old_components = {component["role"]: component for component in old_section["components"]}
            for component in section["components"]:
                old_component = old_components.get(component["role"])
                if old_component is None:
                    findings.append(
                        {"code": "unresolved-component-identity", "extractionId": component["id"]}
                    )
                    continue
                component["id"] = old_component["id"]
                old_blocks = {
                    english_evidence(block["text"]["en"]): block["id"]
                    for group in old_component["groups"]
                    for block in group["blocks"]
                }
                registry_blocks: dict[str, list[str]] = defaultdict(list)
                for entry in (registry or {}).get("entries", []):
                    evidence = entry.get("evidence", {})
                    if (
                        entry.get("kind") == "block"
                        and entry.get("status") == "active"
                        and evidence.get("context") == old_component["id"]
                    ):
                        registry_blocks[evidence.get("englishHash", "")].append(entry["id"])
                old_groups = defaultdict(list)
                for group in old_component["groups"]:
                    old_groups[group["kind"]].append(group["id"])
                group_kind_count: dict[str, int] = defaultdict(int)
                for group in component["groups"]:
                    candidates = old_groups[group["kind"]]
                    index = group_kind_count[group["kind"]]
                    group_kind_count[group["kind"]] += 1
                    if index < len(candidates):
                        group["id"] = candidates[index]
                    else:
                        findings.append(
                            {"code": "unresolved-group-identity", "extractionId": group["id"]}
                        )
                    for block in group["blocks"]:
                        evidence = english_evidence(block["text"]["en"])
                        candidates = registry_blocks.get(evidence, [])
                        stable_id = identity_overrides.get(evidence)
                        if stable_id is None and len(candidates) == 1:
                            stable_id = candidates[0]
                        if stable_id is None:
                            stable_id = old_blocks.get(evidence)
                        if stable_id is None:
                            findings.append(
                                {
                                    "code": "new-block-needs-allocation",
                                    "extractionId": block["id"],
                                    "context": component["id"],
                                }
                            )
                        else:
                            block["id"] = stable_id
    result["idStatus"] = "reconciled-stable-ids"
    result["reconciliationFindings"] = findings
    return result
