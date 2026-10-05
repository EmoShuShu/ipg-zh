from __future__ import annotations

from collections import Counter
from typing import Any

from .core import ROOT, anchor_position_is_legal, sha256_text, validate_schema, walk_nodes
from .omegat import collect_units


def _review_units(documents: list[dict[str, Any]], display_values: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {unit["id"]: (unit["source"], unit["target"])
            for unit in collect_units([(str(index), d) for index, d in enumerate(documents)], display_values)}


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

    for error in validate_schema(manifest, ROOT / "schema/ipg-manifest.schema.json"):
        structural.append({"code": "manifest-schema", "detail": error})

    for document in documents:
        for error in validate_schema(document, ROOT / "schema/ipg-source.schema.json"):
            structural.append({"code": "source-schema", "documentId": document.get("documentId"), "detail": error})

    nodes = [(kind, node) for document in documents for kind, node in walk_nodes([document])]
    annotations = [item for document in documents for item in document.get("publicationAnnotations", [])]
    id_counts = Counter(item.get("id") for _, item in nodes)
    segments_by_block = {}
    for kind, block in nodes:
        if kind != "block" or "readingSegments" not in block:
            continue
        segments = block["readingSegments"]
        segments_by_block[block["id"]] = {segment["id"] for segment in segments}
        cursor = 0
        raw_ids = []
        for segment in segments:
            id_counts[segment["id"]] += 1
            start, end = segment["sourceRange"]
            if start != cursor or end <= start or segment["text"]["en"] != block["text"]["en"][start:end].strip():
                structural.append({"code": "invalid-reading-range", "targetId": segment["id"]})
            cursor = end
            raw_ids.extend(segment["legacyRawUnits"])
            if not segment["text"]["zh"]:
                readiness.append({"code": "missing-translation", "targetId": segment["id"]})
        if cursor != len(block["text"]["en"]) or raw_ids != block.get("legacyRawUnits"):
            structural.append({"code": "incomplete-reading-coverage", "targetId": block["id"]})
        if "\n\n".join(segment["text"]["zh"] for segment in segments) != block["text"]["zh"]:
            structural.append({"code": "reading-target-mismatch", "targetId": block["id"]})
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
        if "segmentId" in anchor and anchor["segmentId"] not in segments_by_block.get(anchor["id"], set()):
            structural.append({"code": "invalid-reading-anchor", "targetId": annotation["id"]})
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

    scope = manifest.get("scope", {})
    official_scope = scope.get("officialContent", {})
    annotation_scope = scope.get("publicationAnnotations", {})
    if official_scope.get("mode") not in {"pilot", "full-document"}:
        structural.append({"code": "invalid-manifest-scope", "targetId": "scope.officialContent.mode"})
    if annotation_scope.get("mode") not in {"pilot", "full-document"}:
        structural.append({"code": "invalid-manifest-scope", "targetId": "scope.publicationAnnotations.mode"})
    if profile == "release":
        if official_scope.get("mode") != "full-document":
            readiness.append({"code": "manifest-not-full-document", "targetId": "scope.officialContent.mode"})
        if manifest.get("publishable") is not True:
            readiness.append({"code": "manifest-not-publishable", "targetId": "publishable"})
        deferred_groups = int(annotation_scope.get("deferredGroups", 0))
        deferred_raw_units = int(annotation_scope.get("deferredRawUnits", 0))
        if deferred_groups or deferred_raw_units:
            readiness.append(
                {
                    "code": "deferred-publication-annotations",
                    "targetId": "scope.publicationAnnotations",
                    "groups": deferred_groups,
                    "rawUnits": deferred_raw_units,
                }
            )

    # Invalid/duplicate content is already a structural failure; still return a report.
    try:
        current_units = _review_units(documents, display_values)
    except ValueError:
        current_units = {}
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
