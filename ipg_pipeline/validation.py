from __future__ import annotations

from collections import Counter
from typing import Any

from .core import ROOT, anchor_position_is_legal, validate_schema, walk_nodes


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
    for item_id, count in sorted(id_counts.items()):
        if count > 1:
            structural.append({"code": "duplicate-id", "targetId": item_id, "count": count})

    known_ids = {node["id"] for _, node in nodes}
    valid_codes = set(display_values.get("values", {}))
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
        if not annotation["text"]["zh"]:
            readiness.append({"code": "missing-translation", "targetId": annotation["id"]})

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

    if review_ledger:
        for entry in review_ledger.get("entries", []):
            if entry.get("status") == "stale":
                readiness.append({"code": "stale-review", "targetId": entry["unitId"]})

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
