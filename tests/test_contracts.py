from copy import deepcopy

from ipg_pipeline.core import (
    ROOT,
    allocate_registry_id,
    anchor_position_is_legal,
    load_yaml,
    update_registry_identity,
    validate_registry,
)


def test_manifest_has_three_version_axes_and_no_profile_or_output_hash() -> None:
    manifest = load_yaml(
        ROOT
        / "src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001/manifest.yaml"
    )
    assert set(manifest["versions"]) == {"official", "annotations", "translation"}
    assert "validationProfile" not in manifest
    assert "rulesSha256" not in str(manifest)


def test_display_values_are_central_and_unique() -> None:
    values = load_yaml(ROOT / "src/ipg/display-values.yaml")["values"]
    assert len(values) == len(set(values))
    assert values["penalty.warning"] == {"en": "Warning", "zh": "警告"}


def test_anchor_types_and_positions() -> None:
    assert anchor_position_is_legal("section", "before")
    assert anchor_position_is_legal("component", "after")
    assert anchor_position_is_legal("group", "inside-start")
    assert anchor_position_is_legal("section", "inside-end")
    assert anchor_position_is_legal("block", "after")
    assert not anchor_position_is_legal("block", "inside-start")
    assert not anchor_position_is_legal("unknown", "before")


def test_registry_new_id_and_retired_id_non_reuse() -> None:
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    assert validate_registry(registry) == []
    allocate_registry_id(
        registry,
        entry_id="ipg-example-new",
        kind="block",
        canonical_key="example:new",
        version="example-v3",
        label="New",
    )
    update_registry_identity(
        registry,
        entry_id="ipg-example-new",
        event="renamed",
        version="example-v4",
        oldLabel="New",
        newLabel="Renamed",
    )
    update_registry_identity(
        registry,
        entry_id="ipg-example-new",
        event="renumbered",
        version="example-v5",
        oldNumber="2.x",
        newNumber="2.y",
    )
    update_registry_identity(
        registry,
        entry_id="ipg-example-new",
        event="retired",
        version="example-v6",
        reason="split",
    )
    try:
        allocate_registry_id(
            registry,
            entry_id="ipg-example-new",
            kind="block",
            canonical_key="example:replacement",
            version="example-v7",
            label="Replacement",
        )
    except ValueError as error:
        assert "may not be reused" in str(error)
    else:
        raise AssertionError("retired id was reused")


def test_override_examples_cover_rename_renumber_split_merge() -> None:
    overrides = load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")["examples"]
    assert set(overrides) == {"rename", "renumber", "split", "merge"}
    assert overrides["split"]["translationInheritance"] == "manual"
    assert overrides["merge"]["translationInheritance"] == "manual"


def test_registry_validation_catches_duplicate_id() -> None:
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    broken = deepcopy(registry)
    broken["entries"].append(deepcopy(broken["entries"][0]))
    assert any("duplicate registry id" in finding for finding in validate_registry(broken))

