"""Reconstruct historical P4 translations from immutable inputs, not edited YAML.

Registry/overrides supply stable identities; only the pinned PDF and legacy
snapshot supply text. Callers receive copies so fixture edits cannot leak.
"""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

from ipg_pipeline.core import ROOT, dump_yaml, load_yaml
from ipg_pipeline.full_migration import migrate_full
from ipg_pipeline.full_parser import extract_full_pdf, parse_full_extraction
from ipg_pipeline.p4 import DOCUMENTS, LEGACY_SOURCE, OFFICIAL_PDF
from ipg_pipeline.reconcile import reconcile_frozen_full_document


@lru_cache(maxsize=1)
def reconstructed_p4() -> tuple[dict, dict, dict]:
    parsed = parse_full_extraction(extract_full_pdf(OFFICIAL_PDF))
    registry = load_yaml(ROOT / "src/ipg/id-registry.yaml")
    overrides = load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")
    reconciled = reconcile_frozen_full_document(parsed, registry, overrides)
    result = migrate_full(reconciled, LEGACY_SOURCE, registry, overrides)
    return parsed, reconciled, result


def legacy_documents() -> list[dict]:
    documents = reconstructed_p4()[2]["documents"]
    return [copy.deepcopy(documents[name]) for name in DOCUMENTS]


def install_legacy_documents(release: Path) -> None:
    """Only for an isolated test repository, never the real release directory."""
    if release.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("historical fixtures must not overwrite repository sources")
    for name, document in zip(DOCUMENTS, legacy_documents(), strict=True):
        (release / name).write_text(dump_yaml(document), encoding="utf-8", newline="\n")
