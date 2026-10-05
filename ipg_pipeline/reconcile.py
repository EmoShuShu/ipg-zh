from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict
from typing import Any

from .core import sha256_text, walk_nodes


def official_records(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten native nodes with structural context; order is evidence, never identity."""
    records = []
    sections = [s for d in documents for s in d["sections"]]
    def visit(kind, node, parent, section, order):
        records.append({"kind": kind, "node": node, "parent": parent, "section": section, "order": order})
        children = {"section": ("components", "component"), "component": ("groups", "group"), "group": ("blocks", "block")}
        if kind in children:
            field, child_kind = children[kind]
            for i, child in enumerate(node[field]):
                visit(child_kind, child, node["id"], section, i)
    for i, section in enumerate(sections):
        visit("section", section, None, section["id"], i)
    return records


def node_identity_hash(kind: str, node: dict[str, Any]) -> str:
    if kind == "block":
        return identity_evidence(node["text"]["en"])
    if kind == "section":
        return identity_evidence(node["title"]["en"])
    if kind == "component":
        return sha256_text(node["role"] + ":" + node["labelCode"])
    return sha256_text(node["kind"] + ":" + node.get("date", ""))


def official_semantics(value: Any) -> Any:
    if isinstance(value, dict):
        excluded = {"id", "documentId", "zh", "officialPdfUnits", "legacyRawUnits", "sourceRawUnits", "readingSegments"}
        return {k: official_semantics(v) for k, v in value.items() if k not in excluded}
    return [official_semantics(v) for v in value] if isinstance(value, list) else value


def override_evidence(node: dict[str, Any]) -> str:
    return sha256_text(json.dumps(official_semantics(node), sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def reconcile_update_document(parsed: dict[str, Any], previous: list[dict[str, Any]],
                              registry: dict[str, Any], *, version: str,
                              overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conservative update coordination, separate from frozen P2/P3 reproduction.

    Exact evidence and unique native parent/role context preserve identity. Modified
    English requires a hash-bound override; old Chinese suggestions remain in findings.
    No positional or fuzzy match is ever promoted into a stable inherited identity.
    """
    result, proposed = copy.deepcopy(parsed), copy.deepcopy(registry)
    old = {r["node"]["id"]: r for r in official_records(previous)}
    new_records = official_records([result["document"]])
    new_by_extraction = {r["node"]["id"]: r for r in new_records}
    entries = {e["id"]: e for e in proposed["entries"]}
    if len(old) != len(official_records(previous)) or len(new_by_extraction) != len(new_records):
        raise ValueError("duplicate official identity")
    if any(i not in entries or entries[i]["status"] != "active" for i in old):
        raise ValueError("prior official identity absent or retired in registry")
    forced, reserved, operations = {}, set(), (overrides or {}).get("mappings", [])
    for operation in operations:
        sources, targets, kind = operation["fromIds"], operation["toExtractionIds"], operation["kind"]
        name = operation["operation"]
        if (name == "split" and not (len(sources) == 1 and len(targets) >= 2)
                or name == "merge" and not (len(sources) >= 2 and len(targets) == 1)
                or name not in {"split", "merge"} and not (len(sources) == len(targets) == 1)):
            raise ValueError("invalid override cardinality")
        if reserved.intersection(sources) or set(forced).intersection(targets):
            raise ValueError("override repeats consumption")
        if set(operation["sourceHashes"]) != set(sources) or set(operation["targetHashes"]) != set(targets):
            raise ValueError("override must bind every source and target hash")
        for source in sources:
            if source not in old or old[source]["kind"] != kind or override_evidence(old[source]["node"]) != operation["sourceHashes"][source]:
                raise ValueError("override source identity/hash changed")
        for target in targets:
            if target not in new_by_extraction or new_by_extraction[target]["kind"] != kind or override_evidence(new_by_extraction[target]["node"]) != operation["targetHashes"][target]:
                raise ValueError("override target identity/hash changed")
        preserve = operation.get("preserveId", sources[0] if name not in {"split", "merge"} else None)
        preserved_target = operation.get("preserveExtractionId", targets[0] if len(targets) == 1 else None)
        if preserve is not None and (preserve not in sources or preserved_target not in targets):
            raise ValueError("split/merge continuity must be explicitly selected")
        forced.update({t: preserve if t == preserved_target else None for t in targets})
        reserved.update(sources)
    mapping, used, uncertain, findings = {}, set(), set(), []

    def child_hashes(kind, node):
        if kind == "section":
            blocks = [b for c in node["components"] for g in c["groups"] for b in g["blocks"]]
        elif kind == "component":
            blocks = [b for g in node["groups"] for b in g["blocks"]]
        else:
            blocks = node.get("blocks", [])
        return Counter(identity_evidence(b["text"]["en"]) for b in blocks)

    for record in new_records:
        node, kind = record["node"], record["kind"]
        extraction_id = node["id"]
        parent = mapping.get(record["parent"])
        eligible = [r for i, r in old.items() if r["kind"] == kind and i not in used and i not in reserved]
        if extraction_id in forced:
            chosen = forced[extraction_id]
            candidates = []
        else:
            exact = [r for r in eligible if node_identity_hash(kind, r["node"]) == node_identity_hash(kind, node)]
            local = [r for r in exact if r["parent"] == parent]
            candidates = local or exact
            if kind == "section" and not candidates:
                evidence = child_hashes(kind, node)
                candidates = [r for r in eligible if r["node"]["kind"] == node["kind"]
                    and evidence and evidence == child_hashes(kind, r["node"])]
            if kind in {"component", "group"} and len(candidates) > 1:
                evidence = child_hashes(kind, node)
                exact_children = [r for r in candidates if evidence and child_hashes(kind, r["node"]) == evidence]
                if exact_children:
                    candidates = exact_children
            chosen = candidates[0]["node"]["id"] if len(candidates) == 1 else None
        if len(candidates) > 1:
            ids = sorted(r["node"]["id"] for r in candidates)
            uncertain.update(ids)
            findings.append({"code": "ambiguous-stable-identity", "kind": kind, "extractionId": extraction_id, "candidateIds": ids})
        if chosen is None:
            prefix = {"section": "ipg-s", "component": "ipg-c", "group": "ipg-g", "block": "ipg-b"}[kind]
            chosen = _next_monotonic_id(proposed, prefix)
            _register(proposed, entry_id=chosen, kind=kind, canonical_key=f"update:{kind}:{chosen}",
                      version=version, label=node.get("title", node.get("text", {})).get("en", kind),
                      evidence={"parent": parent, "englishHash": node_identity_hash(kind, node)})
            entries[chosen] = proposed["entries"][-1]
            if kind == "block" and extraction_id not in forced and not candidates:
                possible = [r for r in eligible if r["parent"] == parent]
                if possible:
                    ids = sorted(r["node"]["id"] for r in possible)
                    uncertain.update(ids)
                    findings.append({"code": "modified-identity-needs-override", "extractionId": extraction_id,
                        "provisionalId": chosen, "candidateIds": ids,
                        "oldTranslations": [{"id": r["node"]["id"], "text": r["node"]["text"]} for r in possible]})
        mapping[extraction_id] = chosen
        node["id"] = chosen
        used.add(chosen)
        prior = old.get(chosen)
        if prior:
            previous_node = prior["node"]
            if kind in {"section", "block"}:
                field = "title" if kind == "section" else "text"
                node[field]["zh"] = previous_node[field]["zh"]
            if kind == "block":
                if previous_node.get("legacyRawUnits"):
                    node["legacyRawUnits"] = copy.deepcopy(previous_node["legacyRawUnits"])
                if previous_node.get("readingSegments"):
                    if previous_node["text"]["en"] == node["text"]["en"] and previous_node["type"] == node["type"]:
                        node["readingSegments"] = copy.deepcopy(previous_node["readingSegments"])
                    else:
                        findings.append({"code": "reading-segmentation-stale", "id": chosen,
                                         "priorSegments": copy.deepcopy(previous_node["readingSegments"])})
            changes = {}
            if kind == "section":
                for field in ("number", "title"):
                    if previous_node[field] != node[field]:
                        changes[field] = {"from": previous_node[field], "to": node[field]}
            if prior["parent"] != parent:
                changes["parent"] = {"from": prior["parent"], "to": parent}
            if node_identity_hash(kind, previous_node) != node_identity_hash(kind, node):
                changes["englishHash"] = {"from": node_identity_hash(kind, previous_node), "to": node_identity_hash(kind, node)}
            if changes:
                entries[chosen].setdefault("history", []).append({"event": "updated", "atVersion": version, "changes": changes})
    # A new paragraph before an unchanged paragraph is an insertion, not a guessed edit.
    for finding in list(findings):
        if finding["code"] == "modified-identity-needs-override":
            finding["candidateIds"] = [i for i in finding["candidateIds"] if i not in used]
            finding["oldTranslations"] = [item for item in finding["oldTranslations"] if item["id"] not in used]
            if not finding["candidateIds"]:
                findings.remove(finding)
    uncertain = {i for f in findings for i in f.get("candidateIds", [])}
    retired = []
    for i in sorted(set(old) - used):
        if i in uncertain:
            findings.append({"code": "retirement-pending-identity", "id": i})
            continue
        entries[i]["status"] = "retired"
        entries[i].setdefault("history", []).append({"event": "retired", "atVersion": version, "reason": "absent after official update; never reusable"})
        retired.append(i)
    for operation in operations:
        if operation["operation"] in {"split", "merge"}:
            targets = [mapping[t] for t in operation["toExtractionIds"]]
            for source in operation["fromIds"]:
                entries[source].setdefault("history", []).append({"event": operation["operation"], "atVersion": version,
                    "successors": targets, "predecessors": operation["fromIds"], "reason": operation["reason"]})
    for record in new_records:
        node = record["node"]
        if node.get("referenceId") in mapping:
            node["referenceId"] = mapping[node["referenceId"]]
    result["document"]["documentId"] = f"ipg-official-update-{version}"
    result["registry"] = proposed
    result["idStatus"] = "reconciled-update-candidate"
    result["reconciliation"] = {"extractionToStableId": mapping, "preservedIds": sorted(set(old) & used),
        "newIds": sorted(used - set(old)), "retiredIds": retired, "findings": findings}
    return result


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


def reconcile_frozen_full_document(
    parsed: dict[str, Any], registry: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Resolve the frozen P3 structure exclusively from registry evidence.

    P4 must be reproducible without an ignored P3 work file or a mutable source
    document acting as an identity seed.  This resolver never allocates: every
    official node must already be represented by the frozen 649-entry registry.
    """
    result = copy.deepcopy(parsed)
    active = [entry for entry in registry.get("entries", []) if entry.get("status") == "active"]
    by_id = {entry["id"]: entry for entry in active}
    by_key = {entry["canonicalKey"]: entry for entry in active}
    block_entries = [entry for entry in active if entry.get("kind") == "block"]

    def registry_block_hashes(entry: dict[str, Any]) -> set[str]:
        hashes = {entry.get("evidence", {}).get("englishHash", "")}
        hashes.update(
            identity_evidence(event["label"])
            for event in entry.get("history", [])
            if event.get("event") == "allocated" and event.get("label")
        )
        return hashes - {""}
    findings: list[dict[str, Any]] = []
    id_remap: dict[str, str] = {}
    resolved: set[str] = set()
    forced_blocks = overrides.get("reconciliation", {}).get("englishHashToStableId", {})

    def choose(kind: str, candidates: list[dict[str, Any]], context: dict[str, Any]) -> str | None:
        unique = {entry["id"]: entry for entry in candidates if entry.get("kind") == kind}
        if len(unique) == 1:
            entry_id = next(iter(unique))
            if entry_id in resolved:
                findings.append({"code": "registry-id-reused", "id": entry_id, **context})
                return None
            resolved.add(entry_id)
            return entry_id
        findings.append(
            {
                "code": "registry-identity-missing" if not unique else "registry-identity-ambiguous",
                "kind": kind,
                "candidateIds": sorted(unique),
                **context,
            }
        )
        return None

    for section in result["document"]["sections"]:
        number = section["number"]
        temporary_section_id = section["id"]
        semantic_section_id = {
            "introduction": "ipg-introduction",
            "framework": "ipg-framework",
            "A": "ipg-app-a",
            "B": "ipg-app-b",
        }.get(number, f"ipg-s{number.replace('.', '-')}")
        section_candidates = [
            entry
            for entry in (
                by_key.get(f"official:section:{number}"),
                by_key.get(f"section:{number}"),
                by_id.get(semantic_section_id),
            )
            if entry is not None
        ]
        stable_section_id = choose("section", section_candidates, {"section": number})
        if stable_section_id is None:
            continue
        section["id"] = stable_section_id
        id_remap[temporary_section_id] = stable_section_id

        for component_ordinal, component in enumerate(section["components"], 1):
            role = component["role"]
            role_suffix = {"appendix-table": "table"}.get(role, role)
            semantic_component_id = f"{stable_section_id}-c-{role_suffix}"
            component_candidates = [
                entry
                for entry in (
                    by_key.get(
                        f"official:component:{stable_section_id}:{role}:{component_ordinal}"
                    ),
                    by_id.get(semantic_component_id),
                )
                if entry is not None
            ]
            stable_component_id = choose(
                "component",
                component_candidates,
                {"section": number, "role": role, "ordinal": component_ordinal},
            )
            if stable_component_id is None:
                continue
            component["id"] = stable_component_id

            for group_ordinal, group in enumerate(component["groups"], 1):
                hashes = {identity_evidence(block["text"]["en"]) for block in group["blocks"]}
                signature = sha256_text("|".join(sorted(hashes)))
                semantic_group_id = f"{stable_component_id}-g{group_ordinal:02d}"
                group_candidates = [
                    entry
                    for entry in (
                        by_key.get(
                            f"official:group:{stable_component_id}:{group['kind']}:{signature}"
                        ),
                        by_id.get(semantic_group_id),
                    )
                    if entry is not None
                ]
                stable_group_id = choose(
                    "group",
                    group_candidates,
                    {
                        "section": number,
                        "component": stable_component_id,
                        "kind": group["kind"],
                        "ordinal": group_ordinal,
                    },
                )
                if stable_group_id is None:
                    continue
                group["id"] = stable_group_id

                for block in group["blocks"]:
                    evidence = identity_evidence(block["text"]["en"])
                    forced_id = forced_blocks.get(evidence)
                    exact_candidates = [by_id[forced_id]] if forced_id in by_id else []
                    for entry in block_entries:
                        if forced_id:
                            continue
                        item_evidence = entry.get("evidence", {})
                        if evidence not in registry_block_hashes(entry):
                            continue
                        exact_candidates.append(entry)
                    candidates = []
                    for entry in exact_candidates:
                        item_evidence = entry.get("evidence", {})
                        contexts = {
                            item_evidence.get("context"),
                            item_evidence.get("component"),
                            item_evidence.get("section"),
                        }
                        if stable_component_id in contexts or stable_section_id in contexts:
                            candidates.append(entry)
                    if not candidates and len(exact_candidates) == 1:
                        candidates = exact_candidates
                    stable_block_id = choose(
                        "block",
                        candidates,
                        {
                            "section": number,
                            "component": stable_component_id,
                            "englishHash": evidence,
                        },
                    )
                    if stable_block_id is not None:
                        block["id"] = stable_block_id

    for _, node in walk_nodes([result["document"]]):
        if node.get("referenceId") in id_remap:
            node["referenceId"] = id_remap[node["referenceId"]]

    result["document"]["documentId"] = "ipg-full-official-2024-09-23"
    result["document"]["publicationAnnotations"] = []
    result["idStatus"] = "reconciled-frozen-registry-ids"
    result["reconciliation"] = {
        "resolvedRegistryIds": sorted(resolved),
        "resolvedRegistryIdCount": len(resolved),
        "findings": findings,
    }
    result["registry"] = copy.deepcopy(registry)
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
