from __future__ import annotations

import copy
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .core import allocate_registry_id, load_yaml, sha256_bytes, sha256_text, walk_nodes


LEGACY_SHA256 = "acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb"
SECTION_RANGES = {
    "2": (651, 720),
    "2.1": (721, 1305),
    "2.2": (1306, 1437),
    "2.3": (1438, 1637),
    "2.4": (1638, 1706),
    "2.5": (1707, 1842),
    "2.6": (1843, 1904),
    "A": (4034, 4064),
    "B": (4065, 4086),
}
TITLE_LINES = {"2": 651, "2.1": 721, "2.2": 1306, "2.3": 1438, "2.4": 1638, "2.5": 1707, "2.6": 1843, "A": 4034, "B": 4065}
LEGACY_TITLE_EN = {
    "2": "Game Play Errors",
    "2.1": "Missed Trigger",
    "2.2": "Looking at Extra Cards",
    "2.3": "Hidden Card Error",
    "2.4": "Game Play Error — Mulligan Procedure Error",
    "2.5": "Game Rule Violation",
    "2.6": "Failure to Maintain Game State",
}
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def tokenize_legacy(path: Path) -> tuple[list[dict[str, Any]], str]:
    data = path.read_bytes()
    actual_hash = sha256_bytes(data)
    if actual_hash != LEGACY_SHA256:
        raise ValueError(f"legacy source hash mismatch: {actual_hash}")
    decoded = data.decode("utf-8")
    segments = decoded.splitlines(keepends=True)
    units: list[dict[str, Any]] = []
    for line_number, segment in enumerate(segments, 1):
        if segment.endswith("\r\n"):
            text, eol = segment[:-2], "CRLF"
        elif segment.endswith("\n"):
            text, eol = segment[:-1], "LF"
        elif segment.endswith("\r"):
            text, eol = segment[:-1], "CR"
        else:
            text, eol = segment, "NONE"
        units.append(
            {
                "id": f"raw-L{line_number:06d}",
                "line": line_number,
                "text": text,
                "eol": eol,
                "sha256": sha256_text(segment),
            }
        )
    reconstructed = "".join(
        unit["text"] + {"CRLF": "\r\n", "LF": "\n", "CR": "\r", "NONE": ""}[unit["eol"]]
        for unit in units
    )
    if reconstructed.encode("utf-8") != data:
        raise AssertionError("legacy tokenizer was not lossless")
    return units, actual_hash


def _normalized(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^>+", "", value).strip()
    value = re.sub(r"^\*\s+", "", value).strip()
    value = re.sub(r"^[A-Z]\.\s+", "", value).strip()
    return " ".join(value.split())


def _unit(units: list[dict[str, Any]], line: int) -> dict[str, Any]:
    return units[line - 1]


def _next_chinese_unit(
    units: list[dict[str, Any]], start_line: int, end_line: int, *, allow_quote: bool = False
) -> dict[str, Any] | None:
    for line_number in range(start_line + 1, end_line + 1):
        candidate = _unit(units, line_number)
        text = candidate["text"].strip()
        if not text:
            continue
        if text.startswith(">") and not allow_quote:
            continue
        if CJK_RE.search(text):
            return candidate
        if not text.startswith(">"):
            return None
    return None


def _consume(
    dispositions: dict[str, dict[str, Any]], unit: dict[str, Any], disposition: str, target_id: str
) -> None:
    if unit["id"] in dispositions:
        old = dispositions[unit["id"]]
        raise ValueError(
            f"duplicate raw-unit consumption: {unit['id']} by {old['targetId']} and {target_id}"
        )
    dispositions[unit["id"]] = {
        "rawUnitId": unit["id"],
        "line": unit["line"],
        "disposition": disposition,
        "targetId": target_id,
    }


def _map_title(
    section: dict[str, Any], units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]]
) -> None:
    number = section["number"]
    source = _unit(units, TITLE_LINES[number])
    raw = re.sub(r"^#+\s*", "", source["text"]).strip()
    if number in {"A", "B"}:
        section["title"]["zh"] = raw.split("～", 1)[1]
    else:
        marker = LEGACY_TITLE_EN[number]
        if marker not in raw:
            raise ValueError(f"legacy title marker changed on line {source['line']}")
        section["title"]["zh"] = raw.split(marker, 1)[1].strip()
    _consume(dispositions, source, "mapped-bilingual-title", section["id"])


def _map_regular_block(
    block: dict[str, Any], number: str, units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]], findings: list[dict[str, Any]]
) -> None:
    start, end = SECTION_RANGES[number]
    wanted = _normalized(block["text"]["en"])
    candidates = [
        unit
        for unit in units[start - 1 : end]
        if not unit["text"].lstrip().startswith(">") and _normalized(unit["text"]) == wanted
    ]
    if len(candidates) != 1:
        finding = "missing-legacy-mapping" if not candidates else "ambiguous-mapping"
        findings.append(
            {
                "code": finding,
                "targetId": block["id"],
                "candidateRawUnits": [unit["id"] for unit in candidates],
            }
        )
        findings.append({"code": "missing-translation", "targetId": block["id"]})
        return
    source = candidates[0]
    translation = _next_chinese_unit(units, source["line"], end)
    if translation is None:
        findings.append({"code": "missing-translation", "targetId": block["id"]})
        _consume(dispositions, source, "mapped-official-en", block["id"])
        return
    block["text"]["zh"] = re.sub(r"^\*\s+", "", translation["text"].strip())
    block["text"]["zh"] = re.sub(r"^[A-Z]\.\s+", "", block["text"]["zh"])
    _consume(dispositions, source, "mapped-official-en", block["id"])
    _consume(dispositions, translation, "mapped-translation", block["id"])


def _map_appendix_a(
    block: dict[str, Any], units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]], findings: list[dict[str, Any]]
) -> None:
    start, end = SECTION_RANGES["A"]
    candidates = []
    for unit in units[start - 1 : end]:
        cells = [cell.strip().strip("*") for cell in unit["text"].strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[1] == block["text"]["en"]:
            candidates.append((unit, cells[0]))
    if len(candidates) != 1:
        findings.append({"code": "ambiguous-mapping" if candidates else "missing-legacy-mapping", "targetId": block["id"]})
        findings.append({"code": "missing-translation", "targetId": block["id"]})
        return
    source, chinese = candidates[0]
    block["text"]["zh"] = chinese
    _consume(dispositions, source, "mapped-bilingual-appendix-row", block["id"])


def _map_appendix_b(
    document: dict[str, Any], units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]], findings: list[dict[str, Any]]
) -> None:
    blocks = next(iter(next(iter(document["sections"]))["components"]))["groups"][0]["blocks"]
    candidates = [
        unit
        for unit in units[4065 - 1 : 4086]
        if unit["text"].lstrip().startswith("*") and CJK_RE.search(unit["text"])
    ][:5]
    if len(candidates) != len(blocks):
        findings.append({"code": "unresolved-mapping", "targetId": "ipg-app-b", "detail": "2024-09-23 entry count changed"})
        return
    for block, source in zip(blocks, candidates, strict=True):
        official_prefix = block["text"]["en"].split(":", 1)[0]
        translated_prefix = source["text"].lstrip("* ").split("：", 1)[0]
        if official_prefix != translated_prefix:
            findings.append({"code": "unresolved-mapping", "targetId": block["id"], "candidateRawUnits": [source["id"]]})
            findings.append({"code": "missing-translation", "targetId": block["id"]})
            continue
        block["text"]["zh"] = source["text"].lstrip("* ")
        _consume(dispositions, source, "mapped-translation-date-and-number", block["id"])


def _resolve_anchor(document: dict[str, Any], anchor: dict[str, Any]) -> dict[str, str]:
    if "id" in anchor:
        return {"type": anchor["type"], "id": anchor["id"]}
    prefix = anchor["matchEnglishPrefix"]
    matches = [
        node["id"]
        for kind, node in walk_nodes([document])
        if kind == anchor["type"] and node.get("text", {}).get("en", "").startswith(prefix)
    ]
    if len(matches) != 1:
        raise ValueError(f"annotation anchor prefix did not resolve uniquely: {prefix}")
    return {"type": anchor["type"], "id": matches[0]}


def _map_annotations(
    document: dict[str, Any], units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]], overrides: dict[str, Any]
) -> None:
    for item in overrides.get("applied", []):
        if item.get("kind") != "publication-annotation-anchor":
            continue
        en_unit = _unit(units, item["legacyLines"][0])
        zh_unit = _unit(units, item["legacyLines"][1])
        annotation_id = f"ipg-ann-{item['id'].removeprefix('pilot-ann-')}"
        annotation = {
            "id": annotation_id,
            "anchor": _resolve_anchor(document, item["anchor"]),
            "position": item["position"],
            "text": {"en": _normalized(en_unit["text"]), "zh": _normalized(zh_unit["text"])},
            "sourceRawUnits": [en_unit["id"], zh_unit["id"]],
        }
        document["publicationAnnotations"].append(annotation)
        _consume(dispositions, en_unit, "mapped-publication-annotation", annotation_id)
        _consume(dispositions, zh_unit, "mapped-publication-annotation-translation", annotation_id)


def _register_nodes(registry: dict[str, Any], documents: list[dict[str, Any]]) -> None:
    existing = {entry["id"] for entry in registry.get("entries", [])}
    live_ids = {
        node["id"]
        for document in documents
        for _, node in (
            list(walk_nodes([document]))
            + [("publication-annotation", item) for item in document["publicationAnnotations"]]
        )
    }
    for entry in registry.get("entries", []):
        if (
            entry.get("status") == "active"
            and entry.get("canonicalKey", "").startswith("pilot:")
            and entry["id"] not in live_ids
        ):
            entry["status"] = "retired"
            entry.setdefault("history", []).append(
                {
                    "event": "retired",
                    "atVersion": "ipg-2024-09-23-pilot",
                    "reason": "pilot structure corrected; id permanently tombstoned",
                }
            )
    for document in documents:
        nodes = list(walk_nodes([document]))
        nodes.extend(("publication-annotation", item) for item in document["publicationAnnotations"])
        for kind, node in nodes:
            if node["id"] in existing:
                continue
            allocate_registry_id(
                registry,
                entry_id=node["id"],
                kind=kind,
                canonical_key=f"pilot:{node['id']}",
                version="ipg-2024-09-23-pilot",
                label=node.get("title", node.get("text", {})).get("en", node["id"]),
            )
            existing.add(node["id"])


def migrate_pilot(
    parsed: dict[str, Any], legacy_path: Path, registry: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    units, legacy_hash = tokenize_legacy(legacy_path)
    documents = copy.deepcopy(parsed["documents"])
    dispositions: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []

    for document in documents.values():
        for section in document["sections"]:
            _map_title(section, units, dispositions)
            for _, node in walk_nodes([{"sections": [section]}]):
                if "officialPdfUnit" not in node:
                    continue
                if section["number"] == "A":
                    _map_appendix_a(node, units, dispositions, findings)
                elif section["number"] != "B":
                    _map_regular_block(node, section["number"], units, dispositions, findings)

    _map_appendix_b(documents["appendix-b.yaml"], units, dispositions, findings)
    _map_annotations(documents["chapter-02.yaml"], units, dispositions, overrides)

    anomaly_lines = {3246, 3248}
    slice_lines = set()
    for start, end in SECTION_RANGES.values():
        slice_lines.update(range(start, min(end, len(units)) + 1))
    for unit in units:
        if unit["id"] in dispositions:
            continue
        text = unit["text"].strip()
        if not text or text == ">":
            disposition = "ignored-layout"
        elif unit["line"] in anomaly_lines:
            disposition = "regression-observed-unpaired-quote-format"
        elif unit["line"] in slice_lines:
            disposition = "deferred-pilot-content"
        else:
            disposition = "out-of-scope"
        dispositions[unit["id"]] = {
            "rawUnitId": unit["id"],
            "line": unit["line"],
            "disposition": disposition,
            "targetId": None,
        }

    if len(dispositions) != len(units):
        raise AssertionError("not every raw unit received exactly one disposition")
    if len(set(dispositions)) != len(units):
        raise AssertionError("raw unit was consumed more than once")
    if not _unit(units, 3246)["text"].startswith(">As explained below"):
        raise ValueError("known line 3246 regression fixture changed")
    if not _unit(units, 3248)["text"].startswith("如下所述"):
        raise ValueError("known line 3248 regression fixture changed")

    for document in documents.values():
        for _, node in walk_nodes([document]):
            if "officialPdfUnit" in node and not node["text"]["zh"]:
                if not any(finding.get("targetId") == node["id"] and finding["code"] == "missing-translation" for finding in findings):
                    findings.append({"code": "missing-translation", "targetId": node["id"]})

    _register_nodes(registry, list(documents.values()))
    ordered_dispositions = [dispositions[unit["id"]] for unit in units]
    return {
        "documents": documents,
        "registry": registry,
        "rawUnits": units,
        "coverageLedger": ordered_dispositions,
        "coverage": {
            "legacySha256": legacy_hash,
            "rawUnitCount": len(units),
            "disposedUnitCount": len(ordered_dispositions),
            "duplicateConsumption": 0,
            "dispositions": dict(sorted(Counter(item["disposition"] for item in ordered_dispositions).items())),
        },
        "findings": findings,
        "findingCounts": dict(sorted(Counter(item["code"] for item in findings).items())),
    }

