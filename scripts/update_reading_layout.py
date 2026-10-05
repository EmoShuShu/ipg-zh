"""Explicit, mechanical reading-layout upgrade; never an automatic project prepare.

Run only after closing OmegaT and backing up source, review, and the whole project.
Existing official text, translations, annotations, and IDs are not rewritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from ipg_pipeline.core import ROOT, dump_yaml, load_yaml
from ipg_pipeline.full_review import full_inputs
from ipg_pipeline.migration import tokenize_legacy
from ipg_pipeline.omegat import _yaml_node_at_pointer
from ipg_pipeline.p4 import LEGACY_SOURCE, RELEASE_DIR
from ipg_pipeline.reading import add_reading_segments


def main() -> None:
    raw, _ = tokenize_legacy(LEGACY_SOURCE)
    registry_path = ROOT / "src/ipg/id-registry.yaml"
    registry = load_yaml(registry_path)
    old_count = len(registry["entries"])
    overrides = load_yaml(ROOT / "src/ipg/mapping-overrides.yaml")
    _, documents, _ = full_inputs()
    updates = {}
    for filename, document in documents:
        original = load_yaml(RELEASE_DIR / filename)
        report = add_reading_segments(document, raw, registry, overrides)
        if report["findings"]:
            raise ValueError(report["findings"])
        path = RELEASE_DIR / filename
        text = path.read_text(encoding="utf-8")
        tree = yaml.compose(text)
        replacements = []
        for si, section in enumerate(document["sections"]):
            for ci, component in enumerate(section["components"]):
                for gi, group in enumerate(component["groups"]):
                    for bi, block in enumerate(group["blocks"]):
                        if block["id"] not in report["splitBlocks"]:
                            continue
                        pointer = ["sections", si, "components", ci, "groups", gi, "blocks", bi]
                        node = _yaml_node_at_pointer(tree, pointer)
                        insertion = text.rfind("\n", 0, node.end_mark.index) + 1
                        indentation = node.start_mark.column
                        addition = "".join(" " * indentation + line + "\n" for line in dump_yaml({"readingSegments": block["readingSegments"]}).splitlines())
                        replacements.append((insertion, addition))
        for index, annotation in enumerate(document["publicationAnnotations"]):
            if annotation["anchor"] == original["publicationAnnotations"][index]["anchor"]:
                continue
            node = _yaml_node_at_pointer(tree, ["publicationAnnotations", index, "anchor"])
            insertion = text.rfind("\n", 0, node.end_mark.index) + 1
            replacements.append((insertion, " " * node.start_mark.column + f"segmentId: {annotation['anchor']['segmentId']}\n"))
        for index, addition in sorted(replacements, reverse=True):
            text = text[:index] + addition + text[index:]
        if yaml.safe_load(text) != document:
            raise ValueError(f"mechanical YAML insertion did not preserve structure: {filename}")
        updates[path] = text
        print(filename, len(report["splitBlocks"]), "split official blocks")
    # An append-only registry update preserves the entire frozen prefix byte-for-byte.
    extra = registry["entries"][old_count:]
    registry_text = registry_path.read_text(encoding="utf-8")
    addition = dump_yaml(extra) if extra else ""
    if yaml.safe_load(registry_text + addition) != registry:
        raise ValueError("registry append failed")
    for path, text in updates.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    registry_path.write_text(registry_text + addition, encoding="utf-8", newline="\n")
    print("allocated reading IDs:", len(extra))


if __name__ == "__main__":
    main()
