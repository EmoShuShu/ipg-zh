import copy
import hashlib
import json

import pytest

from ipg_pipeline.builder import build_outputs
from ipg_pipeline.cli import RELEASE_DIR, build_parser
from ipg_pipeline.core import ROOT, load_yaml, sha256_text, validate_schema
from ipg_pipeline.omegat import collect_units
from ipg_pipeline.review import build_review_ledger
from ipg_pipeline.validation import validate_release


def _inputs() -> tuple[dict, list[dict], dict, dict]:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [load_yaml(RELEASE_DIR / name) for name in manifest["documents"]]
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    migration = {"coverage": {"rawUnitCount": 4085, "disposedUnitCount": 4085, "duplicateConsumption": 0, "dispositions": {}}, "findings": []}
    return manifest, documents, display, migration


def _reviewed_ledger(documents: list[dict], display: dict) -> dict:
    tuples = [(f"doc-{index}.yaml", document) for index, document in enumerate(documents)]
    units = collect_units(tuples, display)
    actions = [{"unitId": unit["id"], "sourceHash": sha256_text(unit["source"]), "targetHash": sha256_text(unit["target"]), "modified": False, "reviewedAt": "2026-10-04"} for unit in units]
    return build_review_ledger(units, actions, release_id="fixture", translation_revision="zh-r0001")


def _complete_manifest(manifest: dict) -> dict:
    result = copy.deepcopy(manifest)
    result["versions"]["annotations"]["licenseStatus"] = "complete"
    result["versions"]["annotations"]["attribution"] = "test-fixture-attribution"
    return result


def test_validation_profile_is_required_by_cli_not_manifest() -> None:
    manifest, _, _, _ = _inputs()
    assert "validationProfile" not in manifest
    with pytest.raises(SystemExit): build_parser().parse_args(["validate"])
    assert build_parser().parse_args(["validate", "--profile", "release"]).profile == "release"


def test_candidate_succeeds_while_same_content_fails_release() -> None:
    manifest, documents, display, migration = _inputs()
    candidate = validate_release(profile="candidate", manifest=manifest, documents=documents, display_values=display, migration_report=migration)
    release = validate_release(profile="release", manifest=manifest, documents=documents, display_values=display, migration_report=migration)
    assert candidate["valid"] is True and candidate["meaning"] == "reviewable-not-publishable"
    assert release["valid"] is False
    assert release["releaseGateCounts"]["missingTranslation"] == 0
    assert release["releaseGateCounts"]["reviewLedgerMissing"] == 1


def test_clean_2_5_fixture_requires_and_passes_with_complete_ledger() -> None:
    manifest, documents, display, _ = _inputs()
    chapter = copy.deepcopy(documents[0]); chapter["sections"] = [section for section in chapter["sections"] if section["number"] == "2.5"]
    clean_migration = {"coverage": {"rawUnitCount": 1, "disposedUnitCount": 1, "duplicateConsumption": 0}, "findings": []}
    ledger = _reviewed_ledger([chapter], display)
    report = validate_release(profile="release", manifest=_complete_manifest(manifest), documents=[chapter], display_values=display, migration_report=clean_migration, review_ledger=ledger)
    assert report["valid"] is True and report["meaning"] == "publishable"
    assert all(value == 0 for value in report["releaseGateCounts"].values())


@pytest.mark.parametrize(("fault", "expected"), [
    ("missing-ledger", "missing-review-ledger"),
    ("unreviewed", "unreviewed"),
    ("stale", "stale-review"),
    ("orphan", "orphan-review-record"),
])
def test_release_review_gate_failures(fault: str, expected: str) -> None:
    manifest, documents, display, migration = _inputs(); manifest = _complete_manifest(manifest)
    if fault == "missing-ledger": ledger = None
    elif fault == "unreviewed": ledger = build_review_ledger(collect_units([(f"d{i}", d) for i, d in enumerate(documents)], display), [], release_id="fixture", translation_revision="zh-r0001")
    elif fault == "stale":
        units = collect_units([(f"d{i}", d) for i, d in enumerate(documents)], display)
        actions = [{"unitId": unit["id"], "sourceHash": sha256_text(unit["source"]), "targetHash": sha256_text(unit["target"]), "modified": False} for unit in units]
        actions[0]["sourceHash"] = "0" * 64
        ledger = build_review_ledger(units, actions, release_id="fixture", translation_revision="zh-r0001")
    else:
        ledger = _reviewed_ledger(documents, display)
        ledger["entries"].append({"unitId": "deleted:unit", "sourceHash": "0" * 64, "targetHash": "0" * 64, "status": "reviewed-unchanged", "reviewedAt": "2026-10-04"})
    report = validate_release(profile="release", manifest=manifest, documents=documents, display_values=display, migration_report=migration, review_ledger=ledger)
    assert report["valid"] is False
    assert expected in report["readinessFindingCounts"]


def test_release_rejects_review_hash_mismatch_and_duplicate_record() -> None:
    manifest, documents, display, migration = _inputs(); ledger = _reviewed_ledger(documents, display)
    ledger["entries"][0]["targetHash"] = "0" * 64
    ledger["entries"].append(copy.deepcopy(ledger["entries"][1]))
    report = validate_release(profile="release", manifest=_complete_manifest(manifest), documents=documents, display_values=display, migration_report=migration, review_ledger=ledger)
    assert "review-target-hash-mismatch" in report["readinessFindingCounts"]
    assert "duplicate-review-record" in report["readinessFindingCounts"]


def test_duplicate_ids_and_invalid_display_codes_are_structural_failures() -> None:
    manifest, documents, display, migration = _inputs(); broken = copy.deepcopy(documents)
    group = broken[0]["sections"][0]["components"][0]["groups"][0]; group["blocks"].append(copy.deepcopy(group["blocks"][0]))
    broken[0]["sections"][1]["penaltyCode"] = "penalty.not-real"
    report = validate_release(profile="candidate", manifest=manifest, documents=broken, display_values=display, migration_report=migration)
    assert report["valid"] is False
    assert report["structuralFindingCounts"]["duplicate-id"] == 1
    assert report["structuralFindingCounts"]["invalid-display-code"] == 1


def test_orphan_annotation_bad_position_and_applies_to_are_rejected() -> None:
    manifest, documents, display, migration = _inputs(); broken = copy.deepcopy(documents)
    annotation = broken[0]["publicationAnnotations"][0]
    annotation["anchor"]["id"] = "ipg-missing"; annotation["position"] = "inside-start"; annotation["appliesTo"] = ["ipg-missing"]
    report = validate_release(profile="candidate", manifest=manifest, documents=broken, display_values=display, migration_report=migration)
    assert report["valid"] is False
    assert report["releaseGateCounts"]["orphanPublicationAnnotation"] == 1
    assert report["structuralFindingCounts"]["invalid-annotation-position"] == 1
    assert report["structuralFindingCounts"]["invalid-annotation-applies-to"] == 1


def test_duplicate_annotation_order_at_same_anchor_is_rejected() -> None:
    manifest, documents, display, migration = _inputs(); broken = copy.deepcopy(documents)
    annotations = broken[0]["publicationAnnotations"]
    annotations[-1]["order"] = annotations[-2]["order"]
    report = validate_release(profile="candidate", manifest=manifest, documents=broken, display_values=display, migration_report=migration)
    assert report["valid"] is False
    assert report["structuralFindingCounts"]["duplicate-annotation-order"] == 1


@pytest.mark.parametrize(("coverage", "expected"), [
    ({"rawUnitCount": 2, "disposedUnitCount": 1, "duplicateConsumption": 0}, "raw-unit-undisposed"),
    ({"rawUnitCount": 2, "disposedUnitCount": 2, "duplicateConsumption": 1}, "duplicate-consumption"),
])
def test_bad_raw_unit_coverage_blocks_candidate(coverage: dict, expected: str) -> None:
    manifest, documents, display, _ = _inputs()
    report = validate_release(profile="candidate", manifest=manifest, documents=documents, display_values=display, migration_report={"coverage": coverage, "findings": []})
    assert report["valid"] is False and expected in report["structuralFindingCounts"]


def test_ambiguous_mapping_is_allowed_in_candidate_but_blocks_release() -> None:
    manifest, documents, display, _ = _inputs()
    migration = {"coverage": {"rawUnitCount": 1, "disposedUnitCount": 1, "duplicateConsumption": 0}, "findings": [{"code": "ambiguous-mapping", "targetId": "ipg-s2"}]}
    candidate = validate_release(profile="candidate", manifest=manifest, documents=documents, display_values=display, migration_report=migration)
    release = validate_release(profile="release", manifest=manifest, documents=documents, display_values=display, migration_report=migration, review_ledger=_reviewed_ledger(documents, display))
    assert candidate["valid"] is True and release["valid"] is False
    assert release["releaseGateCounts"]["unresolvedMapping"] == 1


def test_build_is_byte_deterministic_and_rules_hash_is_external(tmp_path) -> None:
    manifest, documents, display, _ = _inputs(); first, second = tmp_path / "first", tmp_path / "second"
    report_one = build_outputs(first, manifest, documents, display, candidate=True, profile="candidate")
    report_two = build_outputs(second, manifest, documents, display, candidate=True, profile="candidate")
    for name in ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    rules_bytes = (first / "rules.json").read_bytes(); rules_hash = hashlib.sha256(rules_bytes).hexdigest()
    assert rules_hash.encode() not in rules_bytes and report_one == report_two
    assert report_one["outputs"]["rules.json"]["sha256"] == rules_hash
    rules = json.loads(rules_bytes)
    assert rules["candidate"] is True and "rulesSha256" not in str(rules)
    assert validate_schema(rules, ROOT / "schema/ipg-output.schema.json") == []
    assert (first / "IPG.md").read_text(encoding="utf-8").startswith("<!-- CANDIDATE: NOT FOR RELEASE -->")
