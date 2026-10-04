from __future__ import annotations

import copy
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .core import allocate_registry_id, sha256_bytes, sha256_text, walk_nodes
from .reconcile import english_evidence


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
TITLE_LINES = {
    "2": 651,
    "2.1": 721,
    "2.2": 1306,
    "2.3": 1438,
    "2.4": 1638,
    "2.5": 1707,
    "2.6": 1843,
    "A": 4034,
    "B": 4065,
}
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
COMPONENT_LABEL_RE = re.compile(
    r"^\*\*(DEFINITION|EXAMPLES?|PHILOSOPHY|ADDITIONAL REMEDY).+\*\*$", re.I
)


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


def _unit(units: list[dict[str, Any]], line: int) -> dict[str, Any]:
    return units[line - 1]


def _content(text: str) -> str:
    value = re.sub(r"^>+", "", text.strip()).strip()
    value = re.sub(r"^\*\s+", "", value).strip()
    value = re.sub(r"^[A-Z]\.\s+", "", value).strip()
    return value


def _match_norm(text: str) -> str:
    value = _content(text).casefold()
    value = value.replace("��", " ").replace("’", "'").replace("—", " ").replace("–", " ")
    value = re.sub(r"[^\w']+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _is_structural(text: str) -> bool:
    value = text.strip()
    return bool(
        value.startswith("#")
        or value.lower().startswith("*penalty")
        or COMPONENT_LABEL_RE.match(value)
        or value.startswith("|---")
        or value in {"| 违规 | 处罚 |", "| 日期及章节 | 变更 |"}
    )


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


def _body_pairs(
    units: list[dict[str, Any]],
    number: str,
    section_ranges: dict[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    start, end = (section_ranges or SECTION_RANGES)[number]
    pairs: list[dict[str, Any]] = []
    line = start
    while line <= end:
        en = _unit(units, line)
        text = en["text"].strip()
        if not text or text.startswith(">") or CJK_RE.search(text) or _is_structural(text):
            line += 1
            continue
        following = line + 1
        translation = None
        while following <= end:
            candidate = _unit(units, following)
            candidate_text = candidate["text"].strip()
            if not candidate_text or candidate_text == ">" or candidate_text.startswith(">"):
                following += 1
                continue
            if _is_structural(candidate_text):
                following += 1
                continue
            if CJK_RE.search(candidate_text):
                translation = candidate
            break
        if translation is not None:
            pairs.append({"en": en, "zh": translation})
            line = translation["line"] + 1
        else:
            line += 1
    return pairs


def _window_score(wanted: str, pairs: list[dict[str, Any]]) -> float:
    candidate = " ".join(_match_norm(pair["en"]["text"]) for pair in pairs)
    ratio = SequenceMatcher(None, wanted, candidate).ratio()
    wanted_words = set(wanted.split())
    coverage = len(wanted_words & set(candidate.split())) / max(1, len(wanted_words))
    if candidate == wanted:
        return 1.0
    if candidate in wanted or wanted in candidate:
        ratio = max(ratio, min(len(candidate), len(wanted)) / max(len(candidate), len(wanted)))
    return 0.7 * ratio + 0.3 * coverage


def _align_block(
    block: dict[str, Any],
    number: str,
    pairs: list[dict[str, Any]],
    used: set[int],
    dispositions: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
    manual: dict[str, Any] | None = None,
    section_ranges: dict[str, tuple[int, int]] | None = None,
) -> None:
    wanted = _match_norm(block["text"]["en"])
    if manual is not None:
        selected = []
        for legacy_pair in manual["legacyPairs"]:
            index = next(
                (
                    index
                    for index, pair in enumerate(pairs)
                    if pair["en"]["line"] == legacy_pair["enLine"]
                    and pair["zh"]["line"] == legacy_pair["zhLine"]
                ),
                None,
            )
            if index is None or index in used:
                raise ValueError(f"manual body interval no longer resolves: {manual['id']}")
            selected.append(index)
        chosen = [pairs[index] for index in selected]
        translations = [_content(pair["zh"]["text"]) for pair in chosen]
        block["text"]["zh"] = "\n\n".join(translations)
        raw_ids = [item["id"] for pair in chosen for item in (pair["en"], pair["zh"])]
        block["legacyRawUnits"] = raw_ids
        mappings.append(
            {
                "targetId": block["id"],
                "score": "manual-high-confidence",
                "sourceRawUnits": raw_ids,
                "translationSegmentCount": len(translations),
                "chineseCoverage": "complete",
                "overrideId": manual["id"],
            }
        )
        for index in selected:
            used.add(index)
            _consume(dispositions, pairs[index]["en"], "mapped-official-en", block["id"])
            _consume(dispositions, pairs[index]["zh"], "mapped-translation", block["id"])
        return
    candidates: list[tuple[float, int, int]] = []
    for start in range(len(pairs)):
        for length in range(1, min(6, len(pairs) - start) + 1):
            indexes = range(start, start + length)
            if any(index in used for index in indexes):
                continue
            candidates.append((_window_score(wanted, pairs[start : start + length]), start, length))
    candidates.sort(key=lambda item: (-item[0], item[2], item[1]))
    evidence = [
        {
            "score": round(score, 4),
            "rawUnits": [pairs[index]["en"]["id"] for index in range(start, start + length)],
        }
        for score, start, length in candidates[:3]
    ]
    if not candidates or candidates[0][0] < 0.58:
        findings.extend(
            [
                {
                    "code": "missing-legacy-mapping",
                    "targetId": block["id"],
                    "officialText": block["text"]["en"],
                    "searchRange": list((section_ranges or SECTION_RANGES)[number]),
                    "topCandidates": evidence,
                },
                {"code": "missing-translation", "targetId": block["id"]},
            ]
        )
        return
    best_score, start, length = candidates[0]
    if len(candidates) > 1 and candidates[1][0] >= 0.75 and best_score - candidates[1][0] < 0.01:
        findings.extend(
            [
                {
                    "code": "ambiguous-mapping",
                    "targetId": block["id"],
                    "officialText": block["text"]["en"],
                    "searchRange": list((section_ranges or SECTION_RANGES)[number]),
                    "topCandidates": evidence,
                },
                {"code": "missing-translation", "targetId": block["id"]},
            ]
        )
        return

    selected = list(range(start, start + length))
    # A legacy line sometimes contains the complete new paragraph and is followed by
    # separately translated repeated fragments. Include those fragments so no Chinese
    # sentence is silently dropped (the Chapter 2 introduction is the regression case).
    cursor = start + length
    while cursor < len(pairs) and cursor not in used:
        fragment = _match_norm(pairs[cursor]["en"]["text"])
        if fragment and fragment in wanted:
            selected.append(cursor)
            cursor += 1
            continue
        break

    chosen = [pairs[index] for index in selected]
    translations = [_content(pair["zh"]["text"]) for pair in chosen]
    block["text"]["zh"] = "\n\n".join(translations)
    raw_ids = [item["id"] for pair in chosen for item in (pair["en"], pair["zh"])]
    block["legacyRawUnits"] = raw_ids
    mappings.append(
        {
            "targetId": block["id"],
            "score": round(best_score, 4),
            "sourceRawUnits": raw_ids,
            "translationSegmentCount": len(translations),
            "chineseCoverage": "complete",
        }
    )
    for index in selected:
        used.add(index)
        _consume(dispositions, pairs[index]["en"], "mapped-official-en", block["id"])
        _consume(dispositions, pairs[index]["zh"], "mapped-translation", block["id"])


def _map_appendix_a(
    block: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    start, end = SECTION_RANGES["A"]
    candidates = []
    for unit in units[start - 1 : end]:
        cells = [cell.strip().strip("*") for cell in unit["text"].strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[1] == block["text"]["en"]:
            candidates.append((unit, cells[0]))
    if len(candidates) != 1:
        findings.append(
            {
                "code": "ambiguous-mapping" if candidates else "missing-legacy-mapping",
                "targetId": block["id"],
            }
        )
        findings.append({"code": "missing-translation", "targetId": block["id"]})
        return
    source, chinese = candidates[0]
    block["text"]["zh"] = chinese
    block["legacyRawUnits"] = [source["id"]]
    _consume(dispositions, source, "mapped-bilingual-appendix-row", block["id"])


def _map_appendix_b(
    document: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    blocks = document["sections"][0]["components"][0]["groups"][0]["blocks"]
    candidates = [
        unit
        for unit in units[4065 - 1 : 4086]
        if unit["text"].lstrip().startswith("*") and CJK_RE.search(unit["text"])
    ][:5]
    if len(candidates) != len(blocks):
        findings.append(
            {
                "code": "unresolved-mapping",
                "targetId": "ipg-app-b",
                "detail": "2024-09-23 entry count changed",
            }
        )
        return
    for block, source in zip(blocks, candidates, strict=True):
        official_prefix = block["text"]["en"].split(":", 1)[0]
        translated_prefix = source["text"].lstrip("* ").split("：", 1)[0]
        if official_prefix != translated_prefix:
            findings.append(
                {
                    "code": "unresolved-mapping",
                    "targetId": block["id"],
                    "candidateRawUnits": [source["id"]],
                }
            )
            findings.append({"code": "missing-translation", "targetId": block["id"]})
            continue
        block["text"]["zh"] = source["text"].lstrip("* ")
        block["legacyRawUnits"] = [source["id"]]
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
    document: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> None:
    for item in overrides.get("applied", []):
        if item.get("kind") != "publication-annotation-anchor":
            continue
        annotation_id = f"ipg-ann-{item['id'].removeprefix('pilot-ann-')}"
        blocks = []
        for index, legacy_block in enumerate(item["legacyBlocks"], 1):
            en_units = [_unit(units, line) for line in legacy_block["enLines"]]
            zh_units = [_unit(units, line) for line in legacy_block["zhLines"]]
            block_id = f"{annotation_id}-g01-b{index:02d}"
            blocks.append(
                {
                    "id": block_id,
                    "type": "paragraph",
                    "text": {
                        "en": "\n\n".join(_content(unit["text"]) for unit in en_units),
                        "zh": "\n\n".join(_content(unit["text"]) for unit in zh_units),
                    },
                    "sourceRawUnits": [unit["id"] for unit in (*en_units, *zh_units)],
                }
            )
            for unit in en_units:
                _consume(dispositions, unit, "mapped-publication-annotation", block_id)
            for unit in zh_units:
                _consume(
                    dispositions, unit, "mapped-publication-annotation-translation", block_id
                )
        annotation = {
            "id": annotation_id,
            "anchor": _resolve_anchor(document, item["anchor"]),
            "position": item["position"],
            "order": item["order"],
            "groups": [{"id": f"{annotation_id}-g01", "kind": "paragraphs", "blocks": blocks}],
        }
        if item.get("appliesTo"):
            annotation["appliesTo"] = item["appliesTo"]
        document["publicationAnnotations"].append(annotation)


def _annotation_nodes(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    nodes: list[tuple[str, dict[str, Any]]] = []
    for annotation in document["publicationAnnotations"]:
        nodes.append(("publication-annotation", annotation))
        for group in annotation["groups"]:
            nodes.append(("publication-annotation-group", group))
            nodes.extend(("publication-annotation-block", block) for block in group["blocks"])
    return nodes


def _register_nodes(registry: dict[str, Any], documents: list[dict[str, Any]]) -> None:
    existing = {entry["id"] for entry in registry.get("entries", [])}
    all_nodes = [node for document in documents for node in [*walk_nodes([document]), *_annotation_nodes(document)]]
    block_context = {
        block["id"]: component["id"]
        for document in documents
        for section in document["sections"]
        for component in section["components"]
        for group in component["groups"]
        for block in group["blocks"]
    }
    live_ids = {node["id"] for _, node in all_nodes}
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
    for kind, node in all_nodes:
        if node["id"] in existing:
            if kind == "block":
                entry = next(item for item in registry["entries"] if item["id"] == node["id"])
                entry["evidence"] = {
                    "context": block_context[node["id"]],
                    "englishHash": english_evidence(node["text"]["en"]),
                }
            continue
        label = node.get("title", node.get("text", {})).get("en", node["id"])
        allocate_registry_id(
            registry,
            entry_id=node["id"],
            kind=kind,
            canonical_key=f"pilot:{node['id']}",
            version="ipg-2024-09-23-pilot",
            label=label,
        )
        if kind == "block":
            registry["entries"][-1]["evidence"] = {
                "context": block_context[node["id"]],
                "englishHash": english_evidence(node["text"]["en"]),
            }
        existing.add(node["id"])


def _classify_remaining(
    units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]]
) -> None:
    line_to_section = {
        line: number
        for number, (start, end) in SECTION_RANGES.items()
        for line in range(start, end + 1)
    }

    def next_nonblank(line: int, direction: int) -> str:
        cursor = line + direction
        while 1 <= cursor <= len(units):
            value = _unit(units, cursor)["text"].strip()
            if value:
                return value
            cursor += direction
        return ""

    for unit in units:
        if unit["id"] in dispositions:
            continue
        text = unit["text"].strip()
        number = line_to_section.get(unit["line"])
        if unit["line"] in {3246, 3248}:
            disposition = "regression-observed-unpaired-quote-format"
        elif number is None:
            disposition = "out-of-scope"
        elif not text:
            disposition = "ignored-layout"
        elif text == ">":
            disposition = "ignored-quote-separator"
        elif _is_structural(text):
            disposition = "mapped-structural" if number == "2.5" else "ignored-with-reason"
        elif text.startswith(">"):
            disposition = (
                "unresolved-mapping"
                if number == "2.5"
                else "deferred-other-chapter-publication-annotation"
            )
        elif number in {"2", "2.1", "2.2", "2.3", "2.4", "2.6"}:
            adjacent_to_quote = next_nonblank(unit["line"], -1).startswith(">") or next_nonblank(
                unit["line"], 1
            ).startswith(">")
            disposition = (
                "deferred-other-chapter-publication-annotation"
                if adjacent_to_quote
                else "deferred-other-chapter-body"
            )
        elif number in {"A", "B"}:
            disposition = "ignored-with-reason"
        else:
            disposition = "unresolved-mapping"
        dispositions[unit["id"]] = {
            "rawUnitId": unit["id"],
            "line": unit["line"],
            "disposition": disposition,
            "targetId": None,
        }


def migrate_pilot(
    parsed: dict[str, Any], legacy_path: Path, registry: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    units, legacy_hash = tokenize_legacy(legacy_path)
    documents = copy.deepcopy(parsed["documents"])
    dispositions: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    body_overrides = {
        item["targetId"]: item
        for item in overrides.get("applied", [])
        if item.get("kind") == "body-interval"
    }

    for document in documents.values():
        for section in document["sections"]:
            _map_title(section, units, dispositions)
            if section["number"] in {"A", "B"}:
                continue
            pairs = _body_pairs(units, section["number"])
            used: set[int] = set()
            blocks = [
                node
                for kind, node in walk_nodes([{"sections": [section]}])
                if kind == "block"
            ]
            for block in blocks:
                _align_block(
                    block,
                    section["number"],
                    pairs,
                    used,
                    dispositions,
                    findings,
                    mappings,
                    body_overrides.get(block["id"]),
                )

    for _, block in walk_nodes([documents["appendix-a.yaml"]]):
        if "officialPdfUnits" in block:
            _map_appendix_a(block, units, dispositions, findings)
    _map_appendix_b(documents["appendix-b.yaml"], units, dispositions, findings)
    _map_annotations(documents["chapter-02.yaml"], units, dispositions, overrides)

    _classify_remaining(units, dispositions)
    if len(dispositions) != len(units) or len(set(dispositions)) != len(units):
        raise AssertionError("every raw unit must receive exactly one disposition")
    if not _unit(units, 3246)["text"].startswith(">As explained below"):
        raise ValueError("known line 3246 regression fixture changed")
    if not _unit(units, 3248)["text"].startswith("如下所述"):
        raise ValueError("known line 3248 regression fixture changed")

    for document in documents.values():
        for kind, node in walk_nodes([document]):
            if kind == "block" and not node["text"]["zh"]:
                if not any(
                    finding.get("targetId") == node["id"]
                    and finding["code"] == "missing-translation"
                    for finding in findings
                ):
                    findings.append({"code": "missing-translation", "targetId": node["id"]})

    _register_nodes(registry, list(documents.values()))
    ordered_dispositions = [dispositions[unit["id"]] for unit in units]
    pilot_2_5 = [item for item in ordered_dispositions if 1707 <= item["line"] <= 1842]
    deferred_annotation_groups = 0
    in_deferred_annotation = False
    for item in ordered_dispositions:
        in_chapter_two = 651 <= item["line"] <= 1904 and not 1707 <= item["line"] <= 1842
        if not in_chapter_two:
            in_deferred_annotation = False
        elif item["disposition"] == "deferred-other-chapter-publication-annotation":
            if not in_deferred_annotation:
                deferred_annotation_groups += 1
            in_deferred_annotation = True
        elif item["disposition"] not in {"ignored-layout", "ignored-quote-separator"}:
            in_deferred_annotation = False
    return {
        "documents": documents,
        "registry": registry,
        "rawUnits": units,
        "coverageLedger": ordered_dispositions,
        "blockMappings": mappings,
        "coverage": {
            "legacySha256": legacy_hash,
            "rawUnitCount": len(units),
            "disposedUnitCount": len(ordered_dispositions),
            "duplicateConsumption": 0,
            "dispositions": dict(
                sorted(Counter(item["disposition"] for item in ordered_dispositions).items())
            ),
            "section2_5Dispositions": dict(
                sorted(Counter(item["disposition"] for item in pilot_2_5).items())
            ),
            "otherChapterDeferredPublicationAnnotationGroups": deferred_annotation_groups,
        },
        "findings": findings,
        "findingCounts": dict(sorted(Counter(item["code"] for item in findings).items())),
    }
