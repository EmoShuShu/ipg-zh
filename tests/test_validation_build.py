import copy
import hashlib
import json

import pytest

from ipg_pipeline.builder import build_outputs
from ipg_pipeline.cli import RELEASE_DIR, build_parser
from ipg_pipeline.core import ROOT, load_json, load_yaml, validate_schema
from ipg_pipeline.validation import validate_release


def _inputs() -> tuple[dict, list[dict], dict, dict]:
    manifest = load_yaml(RELEASE_DIR / "manifest.yaml")
    documents = [load_yaml(RELEASE_DIR / name) for name in manifest["documents"]]
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    migration = load_json(ROOT / "reports/migration-report.json")
    return manifest, documents, display, migration


def test_validation_profile_is_required_by_cli_not_manifest() -> None:
    manifest, _, _, _ = _inputs()
    assert "validationProfile" not in manifest
    with pytest.raises(SystemExit):
        build_parser().parse_args(["validate"])
    assert build_parser().parse_args(["validate", "--profile", "release"]).profile == "release"


def test_candidate_succeeds_while_same_content_fails_release() -> None:
    manifest, documents, display, migration = _inputs()
    candidate = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report=migration,
    )
    release = validate_release(
        profile="release",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report=migration,
    )
    assert candidate["valid"] is True
    assert candidate["meaning"] == "reviewable-not-publishable"
    assert release["valid"] is False
    assert release["releaseGateCounts"] == {
        "missingTranslation": 24,
        "orphanPublicationAnnotation": 0,
        "unresolvedMapping": 547,
        "duplicateConsumption": 0,
    }


def test_cleared_2_5_fixture_passes_release() -> None:
    manifest, documents, display, _ = _inputs()
    clean_manifest = copy.deepcopy(manifest)
    clean_manifest["versions"]["annotations"]["licenseStatus"] = "complete"
    clean_manifest["versions"]["annotations"]["attribution"] = "test-fixture-attribution"
    chapter = copy.deepcopy(documents[0])
    chapter["sections"] = [section for section in chapter["sections"] if section["number"] == "2.5"]
    clean_migration = {
        "coverage": {"rawUnitCount": 1, "disposedUnitCount": 1, "duplicateConsumption": 0},
        "findings": [],
    }
    report = validate_release(
        profile="release",
        manifest=clean_manifest,
        documents=[chapter],
        display_values=display,
        migration_report=clean_migration,
    )
    assert report["valid"] is True
    assert report["meaning"] == "publishable"
    assert report["releaseGateCounts"] == {
        "missingTranslation": 0,
        "orphanPublicationAnnotation": 0,
        "unresolvedMapping": 0,
        "duplicateConsumption": 0,
    }


def test_duplicate_ids_and_invalid_display_codes_are_structural_failures() -> None:
    manifest, documents, display, migration = _inputs()
    broken = copy.deepcopy(documents)
    group = broken[0]["sections"][0]["components"][0]["groups"][0]
    group["blocks"].append(copy.deepcopy(group["blocks"][0]))
    broken[0]["sections"][1]["penaltyCode"] = "penalty.not-real"
    report = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=broken,
        display_values=display,
        migration_report=migration,
    )
    assert report["valid"] is False
    assert report["structuralFindingCounts"]["duplicate-id"] == 1
    assert report["structuralFindingCounts"]["invalid-display-code"] == 1


def test_orphan_annotation_is_reported_and_bad_position_is_rejected() -> None:
    manifest, documents, display, migration = _inputs()
    broken = copy.deepcopy(documents)
    annotation = broken[0]["publicationAnnotations"][3]
    annotation["anchor"]["id"] = "ipg-missing"
    annotation["position"] = "inside-start"
    report = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=broken,
        display_values=display,
        migration_report=migration,
    )
    assert report["valid"] is False
    assert report["releaseGateCounts"]["orphanPublicationAnnotation"] == 1
    assert report["structuralFindingCounts"]["invalid-annotation-position"] == 1


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        ({"rawUnitCount": 2, "disposedUnitCount": 1, "duplicateConsumption": 0}, "raw-unit-undisposed"),
        ({"rawUnitCount": 2, "disposedUnitCount": 2, "duplicateConsumption": 1}, "duplicate-consumption"),
    ],
)
def test_bad_raw_unit_coverage_blocks_candidate(coverage: dict, expected: str) -> None:
    manifest, documents, display, _ = _inputs()
    report = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report={"coverage": coverage, "findings": []},
    )
    assert report["valid"] is False
    assert expected in report["structuralFindingCounts"]


def test_ambiguous_mapping_is_allowed_in_candidate_but_blocks_release() -> None:
    manifest, documents, display, _ = _inputs()
    migration = {
        "coverage": {"rawUnitCount": 1, "disposedUnitCount": 1, "duplicateConsumption": 0},
        "findings": [{"code": "ambiguous-mapping", "targetId": "ipg-s2"}],
    }
    candidate = validate_release(
        profile="candidate",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report=migration,
    )
    release = validate_release(
        profile="release",
        manifest=manifest,
        documents=documents,
        display_values=display,
        migration_report=migration,
    )
    assert candidate["valid"] is True
    assert release["valid"] is False
    assert release["releaseGateCounts"]["unresolvedMapping"] == 1


def test_build_is_byte_deterministic_and_rules_hash_is_external(tmp_path) -> None:
    manifest, documents, display, _ = _inputs()
    first = tmp_path / "first"
    second = tmp_path / "second"
    report_one = build_outputs(
        first, manifest, documents, display, candidate=True, profile="candidate"
    )
    report_two = build_outputs(
        second, manifest, documents, display, candidate=True, profile="candidate"
    )
    for name in ("IPG.md", "rules.json", "SHA256SUMS", "build-report.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    rules_bytes = (first / "rules.json").read_bytes()
    rules_hash = hashlib.sha256(rules_bytes).hexdigest()
    assert rules_hash.encode() not in rules_bytes
    assert report_one == report_two
    assert report_one["outputs"]["rules.json"]["sha256"] == rules_hash
    rules = json.loads(rules_bytes)
    assert rules["candidate"] is True
    assert "rulesSha256" not in str(rules)
    assert validate_schema(rules, ROOT / "schema/ipg-output.schema.json") == []
    assert (first / "IPG.md").read_text(encoding="utf-8").startswith(
        "<!-- CANDIDATE: NOT FOR RELEASE -->"
    )
