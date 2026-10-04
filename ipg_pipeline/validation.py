from __future__ import annotations

from collections import Counter
from typing import Any

from .core import ROOT, anchor_position_is_legal, sha256_text, validate_schema, walk_nodes


def _review_units(documents: list[dict[str, Any]], display_values: dict[str, Any]) -> dict[str, tuple[str, str]]:
    units = {
        f"display:{code}": (value["en"], value["zh"])
        for code, value in display_values.get("values", {}).items()
    }
    for document in documents:
        for section in document["sections"]:
            units[f"title:{section['id']}"] = (section["title"]["en"], section["title"]["zh"])
            for kind, node in walk_nodes([{"sections": [section]}]):
                if kind == "block":
                    units[f"block:{node['id']}"] = (node["text"]["en"], node["text"]["zh"])
        for annotation in document.get("publicationAnnotations", []):
            for group in annotation["groups"]:
                for block in group["blocks"]:
                    units[f"annotation-block:{block['id']}"] = (
                        block["text"]["en"],
                        block["text"]["zh"],
                    )
    return units


def validate_release(
    *,
    profile: str,
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    display_values: dict[str, Any],
    migration_report: dict[str, Any],
    review_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if profile not in {"candidate", "release"}:
        raise ValueError("profile must be candidate or release")
    structural: list[dict[str, Any]] = []
    readiness: list[dict[str, Any]] = []

    for document in documents:
        for error in validate_schema(document, ROOT / "schema/ipg-source.schema.json"):
            structural.append({"code": "source-schema", "documentId": document.get("documentId"), "detail": error})

    nodes = [(kind, node) for document in documents for kind, node in walk_nodes([document])]
    annotations = [item for document in documents for item in document.get("publicationAnnotations", [])]
    id_counts = Counter(item.get("id") for _, item in nodes)
    id_counts.update(item.get("id") for item in annotations)
    for annotation in annotations:
        for group in annotation.get("groups", []):
            id_counts[group.get("id")] += 1
            for block in group.get("blocks", []):
                id_counts[block.get("id")] += 1
    for item_id, count in sorted(id_counts.items()):
        if count > 1:
            structural.append({"code": "duplicate-id", "targetId": item_id, "count": count})

    known_ids = {node["id"] for _, node in nodes}
    valid_codes = set(display_values.get("values", {}))
    annotation_orders = Counter(
        (
            item["anchor"]["type"],
            item["anchor"]["id"],
            item["position"],
            item["order"],
        )
        for item in annotations
    )
    for key, count in annotation_orders.items():
        if count > 1:
            structural.append(
                {
                    "code": "duplicate-annotation-order",
                    "anchorType": key[0],
                    "anchorId": key[1],
                    "position": key[2],
                    "order": key[3],
                    "count": count,
                }
            )
    for kind, node in nodes:
        for field in ("labelCode", "penaltyCode", "displayCode"):
            if field in node and node[field] not in valid_codes:
                structural.append(
                    {"code": "invalid-display-code", "targetId": node["id"], "field": field, "value": node[field]}
                )
        localized = node.get("title") if kind == "section" else node.get("text") if kind == "block" else None
        if localized is not None and not localized.get("zh", ""):
            readiness.append({"code": "missing-translation", "targetId": node["id"]})

    for code, value in sorted(display_values.get("values", {}).items()):
        if not value.get("zh", ""):
            readiness.append({"code": "missing-translation", "targetId": f"display:{code}"})

    for annotation in annotations:
        anchor = annotation["anchor"]
        if anchor["id"] not in known_ids:
            readiness.append(
                {"code": "orphan-publication-annotation", "targetId": annotation["id"], "anchorId": anchor["id"]}
            )
        if not anchor_position_is_legal(anchor["type"], annotation["position"]):
            structural.append(
                {
                    "code": "invalid-annotation-position",
                    "targetId": annotation["id"],
                    "anchorType": anchor["type"],
                    "position": annotation["position"],
                }
            )
        for applies_to in annotation.get("appliesTo", []):
            if applies_to not in known_ids:
                structural.append(
                    {
                        "code": "invalid-annotation-applies-to",
                        "targetId": annotation["id"],
                        "appliesTo": applies_to,
                    }
                )
        for group in annotation["groups"]:
            for block in group["blocks"]:
                if not block["text"]["zh"]:
                    readiness.append({"code": "missing-translation", "targetId": block["id"]})

    coverage = migration_report.get("coverage", {})
    if coverage.get("rawUnitCount") != coverage.get("disposedUnitCount"):
        structural.append(
            {
                "code": "raw-unit-undisposed",
                "rawUnitCount": coverage.get("rawUnitCount"),
                "disposedUnitCount": coverage.get("disposedUnitCount"),
            }
        )
    duplicate_consumption = int(coverage.get("duplicateConsumption", 0))
    if duplicate_consumption:
        structural.append({"code": "duplicate-consumption", "count": duplicate_consumption})

    mapping_codes = {"ambiguous-mapping", "unresolved-mapping", "missing-legacy-mapping"}
    deferred_units = int(coverage.get("dispositions", {}).get("deferred-pilot-content", 0))
    if deferred_units:
        readiness.append(
            {
                "code": "unresolved-mapping",
                "targetId": "raw-unit-ledger",
                "count": deferred_units,
                "detail": "in-scope legacy units deliberately deferred by the pilot",
            }
        )
    for finding in migration_report.get("findings", []):
        if finding.get("code") in mapping_codes:
            readiness.append(dict(finding))

    annotation_version = manifest.get("versions", {}).get("annotations", {})
    if (
        annotation_version.get("licenseStatus") != "complete"
        or annotation_version.get("attribution") in {None, "", "pending-before-formal-release"}
    ):
        readiness.append({"code": "annotation-license-pending", "targetId": "versions.annotations"})

    current_units = _review_units(documents, display_values)
    if review_ledger is None:
        readiness.append({"code": "missing-review-ledger", "targetId": "review-ledger"})
    else:
        entries = review_ledger.get("entries", [])
        by_id: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            by_id.setdefault(entry.get("unitId", ""), []).append(entry)
        for unit_id, (source, target) in sorted(current_units.items()):
            matches = by_id.get(unit_id, [])
            if not matches:
                readiness.append({"code": "missing-review-record", "targetId": unit_id})
                continue
            if len(matches) != 1:
                readiness.append(
                    {"code": "duplicate-review-record", "targetId": unit_id, "count": len(matches)}
                )
                continue
            entry = matches[0]
            if entry.get("status") == "unreviewed":
                readiness.append({"code": "unreviewed", "targetId": unit_id})
            if entry.get("status") == "stale":
                readiness.append({"code": "stale-review", "targetId": unit_id})
            if entry.get("sourceHash") != sha256_text(source):
                readiness.append({"code": "review-source-hash-mismatch", "targetId": unit_id})
            if entry.get("targetHash") != sha256_text(target):
                readiness.append({"code": "review-target-hash-mismatch", "targetId": unit_id})
        for unit_id in sorted(set(by_id) - set(current_units)):
            readiness.append({"code": "orphan-review-record", "targetId": unit_id})

    def count_codes(items: list[dict[str, Any]]) -> dict[str, int]:
        return dict(sorted(Counter(item["code"] for item in items).items()))

    gate_counts = {
        "missingTranslation": sum(item["code"] == "missing-translation" for item in readiness),
        "orphanPublicationAnnotation": sum(
            item["code"] == "orphan-publication-annotation" for item in readiness
        ),
        "unresolvedMapping": sum(
            item.get("count", 1) for item in readiness if item["code"] in mapping_codes
        ),
        "duplicateConsumption": duplicate_consumption,
        "unreviewed": sum(item["code"] == "unreviewed" for item in readiness),
        "stale": sum(item["code"] == "stale-review" for item in readiness),
        "reviewLedgerMissing": sum(item["code"] == "missing-review-ledger" for item in readiness),
        "orphanReviewRecord": sum(item["code"] == "orphan-review-record" for item in readiness),
    }
    valid = not structural and (profile == "candidate" or not readiness)
    return {
        "schemaVersion": 1,
        "profile": profile,
        "valid": valid,
        "meaning": (
            "reviewable-not-publishable"
            if profile == "candidate" and valid
            else "publishable"
            if valid
            else "validation-failed"
        ),
        "structuralFindingCounts": count_codes(structural),
        "readinessFindingCounts": count_codes(readiness),
        "releaseGateCounts": gate_counts,
        "structuralFindings": structural,
        "readinessFindings": readiness,
    }
