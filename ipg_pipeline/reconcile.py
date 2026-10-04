from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any

from .core import sha256_text, walk_nodes


def english_evidence(text: str) -> str:
    return sha256_text(" ".join(text.casefold().split()))


def identity_evidence(text: str) -> str:
    """Normalize extraction-only layout differences before hashing identity evidence."""
    normalized = re.sub(r"(?<=-)\s+(?=\w)", "", text)
    normalized = re.sub(r"^(upgrade|downgrade):\s*", "", normalized, flags=re.IGNORECASE)
    return english_evidence(normalized)


def _next_monotonic_id(registry: dict[str, Any], prefix: str) -> str:
    numbers = []
    for entry in registry.get("entries", []):
        candidate = entry.get("id", "").removeprefix(prefix)
        if candidate.isdigit():
            numbers.append(int(candidate))
    return f"{prefix}{max(numbers, default=0) + 1:06d}"


def _register(
    registry: dict[str, Any],
    *,
    entry_id: str,
    kind: str,
    canonical_key: str,
    version: str,
    label: str,
    evidence: dict[str, Any],
) -> None:
    registry.setdefault("entries", []).append(
        {
            "id": entry_id,
            "kind": kind,
            "status": "active",
            "canonicalKey": canonical_key,
            "evidence": evidence,
            "history": [{"event": "allocated", "atVersion": version, "label": label}],
        }
    )


def _all_prior_nodes(previous_documents: list[dict[str, Any]]) -> dict[str, Any]:
    sections: dict[str, dict[str, Any]] = {}
    annotations: list[dict[str, Any]] = []
    for document in previous_documents:
        for section in document.get("sections", []):
            sections[section["number"]] = section
        annotations.extend(copy.deepcopy(document.get("publicationAnnotations", [])))
    return {"sections": sections, "annotations": annotations}


def reconcile_full_document(
    parsed: dict[str, Any],
    previous_documents: list[dict[str, Any]],
    registry: dict[str, Any],
    *,
    version: str,
) -> dict[str, Any]:
    """Reconcile a full official extraction without treating parser order as identity.

    P2 nodes are matched from their normalized English evidence and structural context.
    New nodes receive registry-backed monotonic ids. Ambiguous prior evidence fails closed.
    """
    result = copy.deepcopy(parsed)
    registry = copy.deepcopy(registry)
    for entry in registry.get("entries", []):
        if entry.get("kind") != "block":
            continue
        evidence = entry.get("evidence", {})
        digest = evidence.get("englishHash", "")
        for event in entry.get("history", []):
            if event.get("event") == "allocated" and event.get("atVersion") == version and digest:
                event["label"] = f"{evidence.get('section', evidence.get('context', 'official'))} block {digest[:12]}"
    prior = _all_prior_nodes(previous_documents)
    findings: list[dict[str, Any]] = []
    preserved: set[str] = set()
    id_remap: dict[str, str] = {}

    registry_by_key = {entry["canonicalKey"]: entry for entry in registry.get("entries", [])}

    def allocate(kind: str, key: str, label: str, evidence: dict[str, Any]) -> str:
        existing = registry_by_key.get(key)
        if existing and existing.get("status") == "active" and existing.get("kind") == kind:
            return existing["id"]
        prefix = {"component": "ipg-c", "group": "ipg-g", "block": "ipg-b"}[kind]
        entry_id = _next_monotonic_id(registry, prefix)
        _register(
            registry,
            entry_id=entry_id,
            kind=kind,
            canonical_key=key,
            version=version,
            label=label,
            evidence=evidence,
        )
        registry_by_key[key] = registry["entries"][-1]
        return entry_id

    for section in result["document"]["sections"]:
        number = section["number"]
        extraction_section_id = section["id"]
        old_section = prior["sections"].get(number)
        if old_section:
            section["id"] = old_section["id"]
            section["title"]["zh"] = old_section["title"].get("zh", "")
            preserved.add(section["id"])
        else:
            key = f"official:section:{number}"
            existing = registry_by_key.get(key)
            if existing and existing.get("status") == "active":
                section["id"] = existing["id"]
            else:
                semantic = {
                    "introduction": "ipg-introduction",
                    "framework": "ipg-framework",
                    "A": "ipg-app-a",
                    "B": "ipg-app-b",
                }.get(number, f"ipg-s{number.replace('.', '-')}")
                if any(entry.get("id") == semantic for entry in registry.get("entries", [])):
                    findings.append({"code": "stable-id-already-allocated", "id": semantic, "section": number})
                else:
                    _register(
                        registry,
                        entry_id=semantic,
                        kind="section",
                        canonical_key=key,
                        version=version,
                        label=section["title"]["en"],
                        evidence={"number": number, "englishHash": identity_evidence(section["title"]["en"])},
                    )
                    registry_by_key[key] = registry["entries"][-1]
                section["id"] = semantic
        id_remap[extraction_section_id] = section["id"]

        old_components = old_section.get("components", []) if old_section else []
        old_components_by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
        old_blocks_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
        old_group_by_id: dict[str, dict[str, Any]] = {}
        for old_component in old_components:
            old_components_by_role[old_component["role"]].append(old_component)
            for old_group in old_component["groups"]:
                old_group_by_id[old_group["id"]] = old_group
                for old_block in old_group["blocks"]:
                    old_blocks_by_hash[identity_evidence(old_block["text"]["en"])].append(old_block)

        used_components: set[str] = set()
        used_groups: set[str] = set()
        used_blocks: set[str] = set()
        for component_ordinal, component in enumerate(section["components"], 1):
            role = component["role"]
            component_candidates = [
                item for item in old_components_by_role.get(role, []) if item["id"] not in used_components
            ]
            if len(component_candidates) == 1:
                component["id"] = component_candidates[0]["id"]
                old_component = component_candidates[0]
                used_components.add(component["id"])
                preserved.add(component["id"])
            elif len(component_candidates) > 1:
                findings.append(
                    {
                        "code": "ambiguous-component-identity",
                        "section": number,
                        "role": role,
                        "extractionId": component["id"],
                    }
                )
                old_component = None
            else:
                old_component = None
                key = f"official:component:{section['id']}:{role}:{component_ordinal}"
                component["id"] = allocate(
                    "component",
                    key,
                    f"{number} {role}",
                    {"section": section["id"], "role": role, "ordinal": component_ordinal},
                )

            for group_ordinal, group in enumerate(component["groups"], 1):
                group_hashes = {
                    identity_evidence(block["text"]["en"]) for block in group["blocks"]
                }
                scored = []
                for old_group_id, old_group in old_group_by_id.items():
                    if old_group_id in used_groups or old_group["kind"] != group["kind"]:
                        continue
                    old_hashes = {
                        identity_evidence(block["text"]["en"]) for block in old_group["blocks"]
                    }
                    overlap = len(group_hashes & old_hashes)
                    if overlap:
                        scored.append((overlap, old_group_id))
                scored.sort(reverse=True)
                if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                    group["id"] = scored[0][1]
                    used_groups.add(group["id"])
                    preserved.add(group["id"])
                elif scored:
                    findings.append(
                        {
                            "code": "ambiguous-group-identity",
                            "section": number,
                            "extractionId": group["id"],
                            "candidateIds": [item[1] for item in scored if item[0] == scored[0][0]],
                        }
                    )
                else:
                    signature = sha256_text("|".join(sorted(group_hashes)))
                    key = f"official:group:{component['id']}:{group['kind']}:{signature}"
                    group["id"] = allocate(
                        "group",
                        key,
                        f"{number} {role} {group['kind']}",
                        {"component": component["id"], "kind": group["kind"], "englishHash": signature},
                    )

                for block in group["blocks"]:
                    evidence = identity_evidence(block["text"]["en"])
                    candidates = [
                        item for item in old_blocks_by_hash.get(evidence, []) if item["id"] not in used_blocks
                    ]
                    if len(candidates) == 1:
                        old_block = candidates[0]
                        block["id"] = old_block["id"]
                        block["text"]["zh"] = old_block["text"].get("zh", "")
                        if old_block.get("legacyRawUnits"):
                            block["legacyRawUnits"] = old_block["legacyRawUnits"]
                        used_blocks.add(block["id"])
                        preserved.add(block["id"])
                    elif len(candidates) > 1:
                        findings.append(
                            {
                                "code": "ambiguous-block-identity",
                                "section": number,
                                "extractionId": block["id"],
                                "candidateIds": sorted(item["id"] for item in candidates),
                            }
                        )
                    else:
                        key = f"official:block:{section['id']}:{evidence}"
                        block["id"] = allocate(
                            "block",
                            key,
                            f"{section['id']} block {evidence[:12]}",
                            {"section": section["id"], "component": component["id"], "englishHash": evidence},
                        )

        if old_section:
            expected = {
                node["id"]
                for kind, node in walk_nodes([{"sections": [old_section]}])
                if kind in {"section", "component", "group", "block"}
            }
            for missing in sorted(expected - preserved):
                findings.append({"code": "prior-stable-id-not-preserved", "section": number, "id": missing})

    for _, node in walk_nodes([result["document"]]):
        if node.get("referenceId") in id_remap:
            node["referenceId"] = id_remap[node["referenceId"]]

    result["document"]["documentId"] = "ipg-full-official-2024-09-23"
    result["document"]["publicationAnnotations"] = prior["annotations"]
    result["idStatus"] = "reconciled-stable-ids"
    result["reconciliation"] = {
        "preservedPriorIds": sorted(preserved),
        "preservedPriorIdCount": len(preserved),
        "findings": findings,
    }
    result["registry"] = registry
    return result


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
