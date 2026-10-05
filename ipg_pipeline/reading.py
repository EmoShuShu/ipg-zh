"""Persisted bilingual reading divisions; the official PDF block remains intact."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterator

from .core import allocate_registry_id, sha256_text, walk_nodes
from .migration import CJK_RE, _content, _match_norm


def reading_events(document: dict[str, Any]) -> Iterator[tuple[str, dict, list]]:
    """One reading order for Markdown and PO, including all four anchor levels."""
    anchored = defaultdict(list)
    for index, annotation in enumerate(document.get("publicationAnnotations", [])):
        anchor = annotation["anchor"]
        anchored[(anchor["type"], anchor["id"], anchor.get("segmentId"), annotation["position"])].append(
            (annotation, ["publicationAnnotations", index])
        )

    def annotations(kind, node_id, position, segment_id=None):
        for annotation, pointer in sorted(
            anchored.get((kind, node_id, segment_id, position), []), key=lambda pair: pair[0]["order"]
        ):
            yield "annotation", annotation, pointer

    def visit(kind, node, pointer):
        yield from annotations(kind, node["id"], "before")
        if kind == "block":
            if "readingSegments" in node:
                for index, segment in enumerate(node["readingSegments"]):
                    yield from annotations(kind, node["id"], "before", segment["id"])
                    display = {**segment, "type": node["type"] if index == 0 else "paragraph"}
                    if index == 0 and "marker" in node:
                        display["marker"] = node["marker"]
                    yield "reading-segment", display, [*pointer, "readingSegments", index]
                    yield from annotations(kind, node["id"], "after", segment["id"])
            else:
                yield kind, node, pointer
        else:
            yield kind, node, pointer
            yield from annotations(kind, node["id"], "inside-start")
            child_key, child_kind = {"section": ("components", "component"), "component": ("groups", "group"), "group": ("blocks", "block")}[kind]
            for index, child in enumerate(node[child_key]):
                yield from visit(child_kind, child, [*pointer, child_key, index])
            yield from annotations(kind, node["id"], "inside-end")
        yield from annotations(kind, node["id"], "after")

    for index, section in enumerate(document["sections"]):
        yield from visit("section", section, ["sections", index])


def add_reading_segments(document: dict, raw_units: list[dict], registry: dict, overrides: dict) -> dict:
    """Split only proven legacy intervals. Ambiguities are reported, never guessed."""
    raw = {unit["id"]: unit for unit in raw_units}
    explicit = {item["targetId"]: item for item in overrides.get("readingBoundaries", [])}
    split_ids, findings = [], []
    for kind, block in walk_nodes([document]):
        if kind != "block" or "readingSegments" in block:
            continue
        evidence = [raw[rid] for rid in block.get("legacyRawUnits", [])]
        english = [u for u in evidence if not CJK_RE.search(u["text"])]
        chinese = [u for u in evidence if CJK_RE.search(u["text"])]
        annotations = [a for a in document.get("publicationAnnotations", []) if a["anchor"]["id"] == block["id"]]
        annotation_lines = [raw[rid]["line"] for a in annotations for g in a["groups"] for b in g["blocks"] for rid in b.get("sourceRawUnits", [])]
        if len(english) < 2 or not any(english[0]["line"] < line < english[-1]["line"] for line in annotation_lines):
            continue
        source = block["text"]["en"]
        tokens = list(re.finditer(r"[\w']+", source.replace("’", "'")))
        words = [match.group().casefold() for match in tokens]
        cuts = []
        override = explicit.get(block["id"])
        if override:
            if override["sourceHash"] != sha256_text(source):
                raise ValueError(f"reading override source changed: {block['id']}")
            for boundary in override["beforeText"]:
                if source.count(boundary) != 1:
                    raise ValueError(f"reading override is not unique: {block['id']}")
                cuts.append(source.index(boundary))
        else:
            for previous, following in zip(english, english[1:]):
                left = _match_norm(previous["text"]).split()
                candidates = []
                for context_length in (8, 4):
                    right = _match_norm(following["text"]).split()[:context_length]
                    candidates = [i for i in range(1, len(words)) if words[i:i + len(right)] == right and " ".join(words[max(0, i-4):i]) in " ".join(left)]
                    if len(candidates) == 1:
                        break
                if len(candidates) != 1:
                    break
                cuts.append(tokens[candidates[0]].start())
        if len(cuts) != len(english)-1 or cuts != sorted(set(cuts)) or len(english) != len(chinese) or "\n\n".join(_content(u["text"]) for u in chinese) != block["text"]["zh"]:
            findings.append({"code": "unresolved-reading-boundary", "targetId": block["id"], "legacyRawUnits": block["legacyRawUnits"]})
            continue
        starts, ends = [0, *cuts], [*cuts, len(source)]
        segments = []
        for start, end, en, zh in zip(starts, ends, english, chinese, strict=True):
            segment_id = f"{block['id']}-rs-{sha256_text(en['id'] + ':' + zh['id'])[:12]}"
            segment = {"id": segment_id, "sourceRange": [start, end], "text": {"en": source[start:end].strip(), "zh": _content(zh["text"])}, "legacyRawUnits": [en["id"], zh["id"]]}
            segments.append(segment)
            existing = next((entry for entry in registry["entries"] if entry["id"] == segment_id), None)
            key = f"reading-segment:{block['id']}:{en['id']}:{zh['id']}"
            if existing is None:
                allocate_registry_id(registry, entry_id=segment_id, kind="reading-segment", canonical_key=key, version="zh-r0001-reading-layout-v1", label=segment["text"]["en"][:120])
            elif existing["canonicalKey"] != key or existing["status"] != "active":
                raise ValueError(f"reading segment registry conflict: {segment_id}")
        for annotation in annotations:
            lines = [raw[rid]["line"] for g in annotation["groups"] for b in g["blocks"] for rid in b.get("sourceRawUnits", [])]
            if not lines:
                raise ValueError(f"annotation lacks position evidence: {annotation['id']}")
            first, last = min(lines), max(lines)
            # An annotation must lie wholly between body intervals, not overlap them.
            if any(first <= u["line"] <= last for u in evidence):
                raise ValueError(f"annotation overlaps reading evidence: {annotation['id']}")
            preceding = [index for index, unit in enumerate(chinese) if unit["line"] < first]
            if preceding:
                annotation["anchor"]["segmentId"] = segments[preceding[-1]]["id"]
                annotation["position"] = "after"
            else:
                annotation["anchor"]["segmentId"] = segments[0]["id"]
                annotation["position"] = "before"
        block["readingSegments"] = segments
        split_ids.append(block["id"])
    return {"splitBlocks": split_ids, "findings": findings}
