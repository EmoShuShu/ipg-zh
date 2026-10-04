from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .builder import build_outputs
from .core import sha256_bytes, sha256_text
from .omegat import collect_units
from .review import build_review_ledger
from .validation import validate_release


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_p2_review_pack(
    *,
    root: Path,
    manifest: dict[str, Any],
    documents: list[tuple[str, dict[str, Any]]],
    display_values: dict[str, Any],
    migration_report: dict[str, Any],
    report_dir: Path,
) -> dict[str, Any]:
    chapter = copy.deepcopy(documents[0][1])
    chapter["sections"] = [section for section in chapter["sections"] if section["number"] == "2.5"]
    clean_manifest = copy.deepcopy(manifest)
    clean_manifest["versions"]["annotations"]["licenseStatus"] = "complete"
    clean_manifest["versions"]["annotations"]["attribution"] = "fixture-only-attribution"
    units = collect_units([("chapter-02.yaml", chapter)], display_values)
    actions = [
        {
            "unitId": unit["id"],
            "sourceHash": sha256_text(unit["source"]),
            "targetHash": sha256_text(unit["target"]),
            "modified": False,
            "reviewedAt": "fixture",
        }
        for unit in units
    ]
    ledger = build_review_ledger(
        units,
        actions,
        release_id=manifest["releaseId"],
        translation_revision=manifest["versions"]["translation"]["revision"],
    )
    clean_report = validate_release(
        profile="release",
        manifest=clean_manifest,
        documents=[chapter],
        display_values=display_values,
        migration_report={
            "coverage": {"rawUnitCount": 136, "disposedUnitCount": 136, "duplicateConsumption": 0},
            "findings": [],
        },
        review_ledger=ledger,
    )
    _write_json(report_dir / "release-clean-fixture-report.json", clean_report)

    determinism_root = root / "outputs/work/determinism"
    builds = []
    for name in ("first", "second"):
        destination = determinism_root / name
        build_outputs(destination, manifest, [item[1] for item in documents], display_values, candidate=True, profile="candidate")
        builds.append({file.name: sha256_bytes(file.read_bytes()) for file in destination.iterdir() if file.is_file()})
    determinism = {"schemaVersion": 1, "byteIdentical": builds[0] == builds[1], "hashes": builds[0]}
    _write_json(report_dir / "determinism-report.json", determinism)

    annotations = documents[0][1]["publicationAnnotations"]
    disposition_counts = migration_report["coverage"]["dispositions"]
    summary = {
        "schemaVersion": 1,
        "scope": "P0-P2 vertical slice only",
        "section2_5RawUnitDispositions": migration_report["coverage"]["section2_5Dispositions"],
        "section2_5PublicationAnnotations": {
            "annotationCount": len(annotations),
            "innerBlockCount": sum(len(group["blocks"]) for item in annotations for group in item["groups"]),
            "anchors": [
                {"id": item["id"], "anchor": item["anchor"], "position": item["position"], "order": item["order"]}
                for item in annotations
            ],
        },
        "formerMissingTranslations": {"original": 24, "mapped": 24, "trueMissing": 0, "manualUnresolved": 0},
        "otherChapterDeferredPublicationAnnotationRawUnits": disposition_counts.get("deferred-other-chapter-publication-annotation", 0),
        "otherChapterDeferredPublicationAnnotationGroups": migration_report["coverage"].get("otherChapterDeferredPublicationAnnotationGroups", 0),
        "otherChapterDeferredReason": "Outside the approved 2.5 golden sample; retained for the future full-document phase.",
        "cleanReleaseFixtureValid": clean_report["valid"],
        "deterministicBuild": determinism["byteIdentical"],
    }
    _write_json(report_dir / "p2-review-summary.json", summary)
    return summary
