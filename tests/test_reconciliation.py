from copy import deepcopy

from ipg_pipeline.reconcile import english_evidence, reconcile_block_sequence, reconcile_section_identity


def _registry() -> dict:
    return {
        "schemaVersion": 1,
        "entries": [
            {
                "id": f"ipg-b{index:06d}", "kind": "block", "status": "active",
                "canonicalKey": f"block:ipg-b{index:06d}",
                "evidence": {"context": "section:2.5", "englishHash": english_evidence(text)},
                "history": [{"event": "allocated", "atVersion": "v1", "label": text}],
            }
            for index, text in enumerate(("alpha", "beta", "gamma"), 1)
        ],
    }


def _extract(*texts: str) -> list[dict]:
    return [{"extractionId": f"extract-{index}", "text": text} for index, text in enumerate(texts, 1)]


def test_insert_preserves_existing_ids_and_allocates_unused_id() -> None:
    result = reconcile_block_sequence(_extract("alpha", "new", "beta", "gamma"), _registry(), context="section:2.5", version="v2")
    assert [item["id"] for item in result["items"]] == ["ipg-b000001", "ipg-b000004", "ipg-b000002", "ipg-b000003"]
    assert result["findings"] == []


def test_delete_retires_id_and_never_reuses_it() -> None:
    result = reconcile_block_sequence(_extract("alpha", "gamma"), _registry(), context="section:2.5", version="v2")
    retired = next(entry for entry in result["registry"]["entries"] if entry["id"] == "ipg-b000002")
    assert retired["status"] == "retired"
    next_result = reconcile_block_sequence(_extract("alpha", "new", "gamma"), result["registry"], context="section:2.5", version="v3")
    assert [item["id"] for item in next_result["items"]] == ["ipg-b000001", "ipg-b000004", "ipg-b000003"]


def test_reorder_does_not_reassign_by_position() -> None:
    result = reconcile_block_sequence(_extract("gamma", "alpha", "beta"), _registry(), context="section:2.5", version="v2")
    assert [item["id"] for item in result["items"]] == ["ipg-b000003", "ipg-b000001", "ipg-b000002"]


def test_changed_text_needs_override_or_produces_new_identity() -> None:
    without = reconcile_block_sequence(_extract("alpha", "beta renamed", "gamma"), _registry(), context="section:2.5", version="v2")
    assert without["items"][1]["id"] == "ipg-b000004"
    with_override = reconcile_block_sequence(
        _extract("alpha", "beta renamed", "gamma"), _registry(), context="section:2.5", version="v2",
        overrides={"extract-2": "ipg-b000002"},
    )
    assert with_override["items"][1]["id"] == "ipg-b000002"


def test_ambiguous_evidence_produces_finding_instead_of_position_match() -> None:
    registry = _registry()
    duplicate = deepcopy(registry["entries"][0]); duplicate["id"] = "ipg-b000099"; duplicate["canonicalKey"] = "block:ipg-b000099"
    registry["entries"].append(duplicate)
    result = reconcile_block_sequence(_extract("alpha"), registry, context="section:2.5", version="v2")
    assert result["items"][0]["id"] is None
    assert result["findings"][0]["code"] == "ambiguous-identity"


def test_title_change_preserves_section_and_renumber_requires_override() -> None:
    registry = {"entries": [{"id": "ipg-s2-5", "kind": "section", "status": "active", "canonicalKey": "section:2.5"}]}
    renamed = reconcile_section_identity({"extractionId": "extract-s", "number": "2.5", "title": "New title"}, registry)
    assert renamed == {"id": "ipg-s2-5", "findings": []}
    renumbered = reconcile_section_identity({"extractionId": "extract-s", "number": "2.7", "title": "New title"}, registry)
    assert renumbered["id"] is None and renumbered["findings"]
    overridden = reconcile_section_identity({"extractionId": "extract-s", "number": "2.7", "title": "New title"}, registry, override_id="ipg-s2-5")
    assert overridden == {"id": "ipg-s2-5", "findings": []}
