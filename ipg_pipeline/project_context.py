"""Bind legacy maintenance entry points to an explicitly selected repository.

Only command entry points use this single-threaded context. Native production
APIs continue to take a root argument and never depend on the sibling repository.
"""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def project_context(root: Path):
    from . import full_review as review, review_assistant as assistant
    from .core import load_yaml
    from .release import current_release_dir
    root = root.resolve()
    release = current_release_dir(root)
    revision = load_yaml(release / "manifest.yaml")["versions"]["translation"]["revision"]
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", revision):
        raise ValueError("不合法的中文修订号")
    project = root / "outputs/omegat-ipg-full"
    values = dict(ROOT=root, RELEASE_DIR=release, TRANSLATION_REVISION=revision,
        PROJECT_DIR=project, MAPPING_PATH=project / "omegat/full-review.mapping.json",
        GLOSSARY_PATH=root / "terminology/ipg-glossary.txt",
        REVIEW_LEDGER_PATH=root / f"review/status/{revision}.json",
        TRANSLATION_NOTES_PATH=root / f"review/translation-notes/{revision}.json",
        REVIEW_ACTIONS_PATH=root / f"review/actions/{revision}.yaml",
        CURRENT_CANDIDATE=root / "outputs/current-candidate",
        PROGRESS_JSON=root / "outputs/review-status.json", PROGRESS_MARKDOWN=root / "outputs/review-status.md",
        TERMINOLOGY_JSON=root / "outputs/terminology-audit.json", TERMINOLOGY_MARKDOWN=root / "outputs/terminology-audit.md",
        PREVIEW_JSON=project / "import-preview.json", PREVIEW_MARKDOWN=project / "import-preview.md")
    saved = [(module, key, getattr(module, key)) for module in (review, assistant)
             for key in (*values, "PO_ORDER", "EXPECTED_PO_COUNTS", "EXPECTED_UNIT_COUNT") if hasattr(module, key)]
    try:
        for module, key, old in saved:
            if key in values:
                setattr(module, key, values[key])
        counts = {name: len(units) for name, units in review.units_by_po(*review.full_inputs()[1:]).items()}
        review.PO_ORDER = assistant.PO_ORDER = list(counts)
        review.EXPECTED_PO_COUNTS = counts
        review.EXPECTED_UNIT_COUNT = sum(counts.values())
        yield
    finally:
        for module, key, old in saved:
            setattr(module, key, old)
