from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .core import allocate_registry_id, sha256_text, walk_nodes
from .migration import (
    CJK_RE,
    _align_block,
    _body_pairs,
    _content,
    _is_structural,
    _match_norm,
    _unit,
    tokenize_legacy,
)


SECTION_STARTS = [
    ("introduction", 71), ("framework", 77), ("1", 95), ("1.1", 305),
    ("1.2", 439), ("1.3", 509), ("1.4", 543), ("1.5", 625),
    ("2", 651), ("2.1", 721), ("2.2", 1306), ("2.3", 1438),
    ("2.4", 1638), ("2.5", 1707), ("2.6", 1843), ("3", 1905),
    ("3.1", 1955), ("3.2", 2076), ("3.3", 2178), ("3.4", 2294),
    ("3.5", 2482), ("3.6", 2730), ("3.7", 2796), ("3.8", 2876),
    ("3.9", 2996), ("4", 3088), ("4.1", 3122), ("4.2", 3242),
    ("4.3", 3534), ("4.4", 3632), ("4.5", 3738), ("4.6", 3798),
    ("4.7", 3866), ("4.8", 3934), ("A", 4034), ("B", 4065),
]
SECTION_RANGES = {
    number: (line, SECTION_STARTS[index + 1][1] - 1 if index + 1 < len(SECTION_STARTS) else 4085)
    for index, (number, line) in enumerate(SECTION_STARTS)
}
TITLE_LINES = dict(SECTION_STARTS)
DOCUMENT_LAYOUT = [
    ("front-matter.yaml", "ipg-front-matter", {"introduction", "framework"}),
    ("chapter-01.yaml", "ipg-chapter-01", {"1", "1.1", "1.2", "1.3", "1.4", "1.5"}),
    ("chapter-02.yaml", "ipg-chapter-02", {"2", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"}),
    ("chapter-03.yaml", "ipg-chapter-03", {"3", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "3.9"}),
    ("chapter-04.yaml", "ipg-chapter-04", {"4", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8"}),
    ("appendix-a.yaml", "ipg-appendix-a", {"A"}),
    ("appendix-b.yaml", "ipg-appendix-b", {"B"}),
]
ORDERED_MARKER_RE = re.compile(r"^(\d+|[A-Z])\.\s+(.*)$")


def _dispose(
    dispositions: dict[str, dict[str, Any]],
    unit: dict[str, Any],
    disposition: str,
    *,
    target_id: str | None = None,
    reason: str | None = None,
) -> None:
    if unit["id"] in dispositions:
        prior = dispositions[unit["id"]]
        raise ValueError(
            f"duplicate raw-unit consumption: {unit['id']} by {prior.get('targetId')} and {target_id}"
        )
    record = {
        "rawUnitId": unit["id"],
        "line": unit["line"],
        "disposition": disposition,
        "targetId": target_id,
    }
    if reason:
        record["reason"] = reason
    dispositions[unit["id"]] = record


def _legacy_title(text: str) -> str:
    raw = re.sub(r"^#+\s*", "", text).strip()
    if "～" in raw:
        return raw.split("～", 1)[1].strip()
    match = CJK_RE.search(raw)
    if not match:
        raise ValueError(f"legacy title has no Chinese text: {text}")
    return raw[match.start() :].strip()


def _blocks(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for kind, node in walk_nodes([{"sections": [section]}]) if kind == "block"]


def _annotation_nodes(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for annotation in document.get("publicationAnnotations", []):
        result.append(("publication-annotation", annotation))
        for group in annotation["groups"]:
            result.append(("publication-annotation-group", group))
            result.extend(("publication-annotation-block", block) for block in group["blocks"])
    return result


def _consume_existing_provenance(
    document: dict[str, Any],
    units_by_id: dict[str, dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
) -> None:
    for kind, node in walk_nodes([document]):
        if kind != "block":
            continue
        for raw_id in node.get("legacyRawUnits", []):
            unit = units_by_id[raw_id]
            _dispose(
                dispositions,
                unit,
                "mapped-translation" if CJK_RE.search(unit["text"]) else "mapped-official-en",
                target_id=node["id"],
            )
    for _, node in _annotation_nodes(document):
        if not node["id"].startswith("ipg-ann-") or "sourceRawUnits" not in node:
            continue
        for raw_id in node["sourceRawUnits"]:
            unit = units_by_id[raw_id]
            _dispose(
                dispositions,
                unit,
                "mapped-publication-annotation-translation"
                if CJK_RE.search(unit["text"])
                else "mapped-publication-annotation",
                target_id=node["id"],
            )


def _mark_titles(
    document: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
) -> None:
    for section in document["sections"]:
        unit = _unit(units, TITLE_LINES[section["number"]])
        translated = _legacy_title(unit["text"])
        if section["title"]["zh"] and section["title"]["zh"] != translated:
            raise ValueError(f"existing title translation changed for {section['id']}")
        section["title"]["zh"] = translated
        _dispose(dispositions, unit, "mapped-bilingual-title", target_id=section["id"])


def _manual_body_overrides(overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["targetId"]: item
        for item in overrides.get("applied", [])
        if item.get("kind") == "body-interval"
    }


def _map_body(
    document: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
    findings: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> None:
    body_overrides = _manual_body_overrides(overrides)
    for section in document["sections"]:
        number = section["number"]
        if number in {"A", "B"}:
            continue
        pairs = _body_pairs(units, number, SECTION_RANGES)
        pair_by_raw = {
            raw_id: index
            for index, pair in enumerate(pairs)
            for raw_id in (pair["en"]["id"], pair["zh"]["id"])
        }
        used = {
            pair_by_raw[raw_id]
            for block in _blocks(section)
            for raw_id in block.get("legacyRawUnits", [])
            if raw_id in pair_by_raw
        }
        for block in _blocks(section):
            if block.get("legacyRawUnits"):
                continue
            _align_block(
                block,
                number,
                pairs,
                used,
                dispositions,
                findings,
                mappings,
                body_overrides.get(block["id"]),
                SECTION_RANGES,
            )
        for index, pair in enumerate(pairs):
            if index in used:
                continue
            for unit in (pair["en"], pair["zh"]):
                if unit["id"] not in dispositions:
                    _dispose(
                        dispositions,
                        unit,
                        "ignored-obsolete-official-version",
                        target_id=section["id"],
                        reason="legacy official wording has no current-PDF block after complete alignment",
                    )


def _apply_legacy_context_overrides(
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> list[str]:
    applied = []
    for item in overrides.get("applied", []):
        if item.get("kind") != "legacy-official-context":
            continue
        for raw_id in item["legacyRawUnits"]:
            _dispose(
                dispositions,
                next(unit for unit in units if unit["id"] == raw_id),
                "mapped-obsolete-official-context",
                target_id=item["targetId"],
                reason=item["rationale"],
            )
        applied.append(item["id"])
    return applied


def _map_appendix_a(
    section: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
) -> None:
    rows = []
    for unit in units[SECTION_RANGES["A"][0] - 1 : SECTION_RANGES["A"][1]]:
        cells = [cell.strip().strip("*") for cell in unit["text"].strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] and cells[1] and not cells[0].startswith("-"):
            rows.append((unit, cells[0], cells[1]))
    for block in _blocks(section):
        if block.get("legacyRawUnits"):
            continue
        wanted = _match_norm(block["text"]["en"])
        matches = [(unit, zh) for unit, zh, en in rows if _match_norm(en) == wanted]
        if len(matches) != 1:
            raise ValueError(f"appendix A row did not resolve uniquely: {block['id']}")
        unit, translation = matches[0]
        block["text"]["zh"] = translation
        block["legacyRawUnits"] = [unit["id"]]
        _dispose(dispositions, unit, "mapped-bilingual-appendix-row", target_id=block["id"])


def _map_appendix_b(
    section: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    legacy_entries = [
        unit
        for unit in units[SECTION_RANGES["B"][0] - 1 : SECTION_RANGES["B"][1]]
        if unit["text"].lstrip().startswith("*")
    ]
    translated_entries = [unit for unit in legacy_entries if CJK_RE.search(unit["text"])]
    september_group = next(
        group
        for component in section["components"]
        for group in component["groups"]
        if group.get("date") == "September 23, 2024"
    )
    if len(translated_entries) != len(september_group["blocks"]):
        raise ValueError("legacy Appendix B translated entry count changed")
    for block, unit in zip(september_group["blocks"], translated_entries, strict=True):
        official_prefix = block["text"]["en"].split(":", 1)[0]
        translated_prefix = unit["text"].lstrip("* ").split("：", 1)[0]
        if official_prefix != translated_prefix:
            raise ValueError(
                f"legacy Appendix B translated entry no longer matches {block['id']}"
            )
        block["text"]["zh"] = unit["text"].lstrip("* ")
        block["legacyRawUnits"] = [unit["id"]]
        _dispose(
            dispositions,
            unit,
            "mapped-translation-date-and-number",
            target_id=block["id"],
        )

    unmatched = [unit for unit in legacy_entries if unit not in translated_entries]
    for block in _blocks(section):
        if block.get("legacyRawUnits"):
            raw_ids = set(block["legacyRawUnits"])
            unmatched = [unit for unit in unmatched if unit["id"] not in raw_ids]
            continue
        wanted = _match_norm(block["text"]["en"])
        matches = [unit for unit in unmatched if _match_norm(unit["text"].lstrip("* ")) == wanted]
        if len(matches) == 1:
            unit = matches[0]
            unmatched.remove(unit)
            _dispose(
                dispositions,
                unit,
                "mapped-legacy-english-without-translation",
                target_id=block["id"],
                reason="legacy appendix entry is English-only; zh intentionally remains empty",
            )
        findings.append(
            {
                "code": "missing-translation",
                "targetId": block["id"],
                "officialText": block["text"]["en"],
                "searchRange": list(SECTION_RANGES["B"]),
                "legacyEnglishEvidence": [unit["id"] for unit in matches],
                "reason": "no legacy Chinese translation exists",
            }
        )
    if unmatched:
        raise ValueError(f"unmatched Appendix B legacy entries: {[unit['id'] for unit in unmatched]}")


def _mark_structural_and_preamble(
    units: list[dict[str, Any]], dispositions: dict[str, dict[str, Any]]
) -> None:
    for unit in units:
        if unit["id"] in dispositions:
            continue
        text = unit["text"].strip()
        if unit["line"] <= 70:
            reason = "legacy-version-notes" if unit["line"] <= 30 else "legacy-table-of-contents"
            _dispose(dispositions, unit, "ignored-with-reason", reason=reason)
        elif not text:
            continue
        elif text == ">":
            continue
        elif SECTION_RANGES["A"][0] <= unit["line"] <= SECTION_RANGES["A"][1] and text.startswith("|"):
            _dispose(
                dispositions,
                unit,
                "ignored-with-reason",
                reason="legacy Appendix A table header or category row",
            )
        elif (
            _is_structural(text)
            or (text.startswith("**") and text.endswith("**"))
            or re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", text)
            or text.startswith("|-----")
        ):
            _dispose(
                dispositions,
                unit,
                "ignored-with-reason",
                reason="legacy structural label or table/date heading",
            )


def _strip_annotation_line(text: str) -> str:
    return re.sub(r"^>\s?", "", text).strip()


def _annotation_piece(units: list[dict[str, Any]]) -> dict[str, Any]:
    raw_parts = [_strip_annotation_line(unit["text"]) for unit in units]
    joined = " ".join(part for part in raw_parts if part).strip()
    marker = None
    block_type = "paragraph"
    if joined.startswith("* "):
        marker, joined, block_type = "bullet", joined[2:].strip(), "list-item"
    else:
        ordered = ORDERED_MARKER_RE.match(joined)
        if ordered:
            marker, joined, block_type = ordered.group(1), ordered.group(2), "list-item"
    return {"units": units, "text": joined, "type": block_type, "marker": marker}


def _logical_annotation_pieces(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pieces = []
    current = []
    for unit in chunk:
        stripped = unit["text"].strip()
        if not stripped:
            continue
        if stripped == ">":
            if current:
                pieces.append(_annotation_piece(current))
                current = []
            continue
        current.append(unit)
    if current:
        pieces.append(_annotation_piece(current))
    return pieces


def _annotation_chunks(
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    start: int,
    end: int,
) -> list[list[dict[str, Any]]]:
    chunks = []
    current = []
    for unit in units[start - 1 : end]:
        if unit["id"] in dispositions:
            if current and any(item["text"].strip() not in {"", ">"} for item in current):
                chunks.append(current)
            current = []
        else:
            current.append(unit)
    if current and any(item["text"].strip() not in {"", ">"} for item in current):
        chunks.append(current)
    return chunks


def _piece_content(piece: dict[str, Any]) -> str:
    return piece["text"]


def _annotation_override_by_start(overrides: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        item["startLine"]: item
        for item in overrides.get("applied", [])
        if item.get("kind") == "publication-annotation-boundaries"
    }


def _map_pilot_annotation_overrides(
    document: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> list[str]:
    """Recreate the approved P2 annotations from their durable override evidence."""
    known_ids = {node["id"] for _, node in walk_nodes([document])}
    existing_raw_sets = {
        frozenset(
            raw_id
            for group in annotation["groups"]
            for block in group["blocks"]
            for raw_id in block.get("sourceRawUnits", [])
        )
        for annotation in document["publicationAnnotations"]
    }
    applied = []
    for item in overrides.get("applied", []):
        if item.get("kind") != "publication-annotation-anchor":
            continue
        all_raw = frozenset(
            f"raw-L{line:06d}"
            for block in item["legacyBlocks"]
            for line in (*block["enLines"], *block["zhLines"])
        )
        if all_raw in existing_raw_sets:
            continue
        if item["anchor"]["id"] not in known_ids:
            raise ValueError(f"publication annotation anchor does not exist: {item['id']}")
        annotation_id = f"ipg-ann-{item['id'].removeprefix('pilot-ann-')}"
        group_id = f"{annotation_id}-g01"
        blocks = []
        for index, legacy_block in enumerate(item["legacyBlocks"], 1):
            en_units = [_unit(units, line) for line in legacy_block["enLines"]]
            zh_units = [_unit(units, line) for line in legacy_block["zhLines"]]
            block_id = f"{group_id}-b{index:02d}"
            block = {
                "id": block_id,
                "type": "paragraph",
                "text": {
                    "en": "\n\n".join(_content(unit["text"]) for unit in en_units),
                    "zh": "\n\n".join(_content(unit["text"]) for unit in zh_units),
                },
                "sourceRawUnits": [unit["id"] for unit in (*en_units, *zh_units)],
            }
            blocks.append(block)
            for unit in en_units:
                _dispose(dispositions, unit, "mapped-publication-annotation", target_id=block_id)
            for unit in zh_units:
                _dispose(
                    dispositions,
                    unit,
                    "mapped-publication-annotation-translation",
                    target_id=block_id,
                )
        annotation = {
            "id": annotation_id,
            "anchor": copy.deepcopy(item["anchor"]),
            "position": item["position"],
            "order": item["order"],
            "groups": [{"id": group_id, "kind": "paragraphs", "blocks": blocks}],
        }
        if item.get("appliesTo"):
            annotation["appliesTo"] = copy.deepcopy(item["appliesTo"])
        document["publicationAnnotations"].append(annotation)
        existing_raw_sets.add(all_raw)
        applied.append(item["id"])
    return applied


def _pieces_from_override(item: dict[str, Any], units: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    english = []
    chinese = []
    for block in item["legacyBlocks"]:
        en_units = [_unit(units, line) for line in block["enLines"]]
        zh_units = [_unit(units, line) for line in block["zhLines"]]
        en_piece = _annotation_piece(en_units)
        zh_piece = _annotation_piece(zh_units)
        if block.get("marker"):
            en_piece["marker"] = zh_piece["marker"] = block["marker"]
            en_piece["type"] = zh_piece["type"] = "list-item"
        english.append(en_piece)
        chinese.append(zh_piece)
    return english, chinese


def _annotation_anchor(
    section: dict[str, Any], start_line: int, override: dict[str, Any] | None
) -> tuple[dict[str, str], str, int]:
    if override:
        return override["anchor"], override["position"], override["order"]
    preceding = []
    for block in _blocks(section):
        lines = [int(raw_id.removeprefix("raw-L")) for raw_id in block.get("legacyRawUnits", [])]
        if lines and max(lines) < start_line:
            preceding.append((max(lines), block["id"]))
    if not preceding:
        return {"type": "section", "id": section["id"]}, "inside-start", 10
    return {"type": "block", "id": max(preceding)[1]}, "after", 0


def _map_annotations(
    document: dict[str, Any],
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
    findings: list[dict[str, Any]],
) -> list[str]:
    applied_overrides = _map_pilot_annotation_overrides(
        document, units, dispositions, overrides
    )
    override_by_start = _annotation_override_by_start(overrides)
    existing_raw_sets = {
        frozenset(raw_id for group in annotation["groups"] for block in group["blocks"] for raw_id in block.get("sourceRawUnits", []))
        for annotation in document["publicationAnnotations"]
    }
    order_by_anchor = defaultdict(int)
    for annotation in document["publicationAnnotations"]:
        key = (annotation["anchor"]["type"], annotation["anchor"]["id"], annotation["position"])
        order_by_anchor[key] = max(order_by_anchor[key], annotation["order"])

    for section in document["sections"]:
        if section["number"] in {"A", "B", "introduction", "framework"}:
            continue
        for chunk in _annotation_chunks(units, dispositions, *SECTION_RANGES[section["number"]]):
            meaningful = [unit for unit in chunk if unit["text"].strip() not in {"", ">"}]
            start_line = meaningful[0]["line"]
            override = override_by_start.get(start_line)
            pieces = _logical_annotation_pieces(chunk)
            if override:
                english, chinese = _pieces_from_override(override, units)
                applied_overrides.append(override["id"])
            else:
                split = next((index for index, piece in enumerate(pieces) if CJK_RE.search(piece["text"])), len(pieces))
                english, chinese = pieces[:split], pieces[split:]
            if not english or len(english) != len(chinese):
                findings.append(
                    {
                        "code": "unresolved-annotation-mapping",
                        "section": section["number"],
                        "rawUnits": [unit["id"] for unit in meaningful],
                        "englishBlocks": len(english),
                        "chineseBlocks": len(chinese),
                    }
                )
                continue
            all_raw = frozenset(unit["id"] for piece in [*english, *chinese] for unit in piece["units"])
            if all_raw in existing_raw_sets:
                continue
            anchor, position, fixed_order = _annotation_anchor(section, start_line, override)
            order_key = (anchor["type"], anchor["id"], position)
            order = fixed_order or order_by_anchor[order_key] + 10
            if order <= order_by_anchor[order_key]:
                order = order_by_anchor[order_key] + 10
            order_by_anchor[order_key] = order
            digest = sha256_text("|".join(sorted(all_raw)))[:12]
            annotation_id = f"ipg-ann-{section['number'].replace('.', '-')}-{digest}"
            paired = []
            for en_piece, zh_piece in zip(english, chinese, strict=True):
                if en_piece["type"] != zh_piece["type"] and not override:
                    findings.append(
                        {
                            "code": "ambiguous-annotation-block-kind",
                            "section": section["number"],
                            "rawUnits": [unit["id"] for unit in (*en_piece["units"], *zh_piece["units"])],
                        }
                    )
                    paired = []
                    break
                paired.append((en_piece, zh_piece))
            if not paired:
                continue
            groups = []
            group_kind = None
            group_blocks = []

            def finish_group() -> None:
                nonlocal group_blocks, group_kind
                if not group_blocks:
                    return
                group_index = len(groups) + 1
                group_id = f"{annotation_id}-g{group_index:02d}"
                for block_index, block in enumerate(group_blocks, 1):
                    block["id"] = f"{group_id}-b{block_index:02d}"
                groups.append({"id": group_id, "kind": group_kind, "blocks": group_blocks})
                group_blocks = []

            for en_piece, zh_piece in paired:
                kind = (
                    "unordered-list"
                    if en_piece["marker"] == "bullet"
                    else "ordered-list"
                    if en_piece["marker"]
                    else "paragraphs"
                )
                if group_blocks and kind != group_kind:
                    finish_group()
                group_kind = kind
                block = {
                    "id": "pending",
                    "type": en_piece["type"],
                    "text": {"en": _piece_content(en_piece), "zh": _piece_content(zh_piece)},
                    "sourceRawUnits": [unit["id"] for unit in (*en_piece["units"], *zh_piece["units"])],
                }
                if en_piece["marker"]:
                    block["marker"] = en_piece["marker"]
                group_blocks.append(block)
            finish_group()
            annotation = {
                "id": annotation_id,
                "anchor": anchor,
                "position": position,
                "order": order,
                "groups": groups,
            }
            document["publicationAnnotations"].append(annotation)
            for group in groups:
                for block in group["blocks"]:
                    for raw_id in block["sourceRawUnits"]:
                        unit = next(item for item in units if item["id"] == raw_id)
                        _dispose(
                            dispositions,
                            unit,
                            "mapped-publication-annotation-translation"
                            if CJK_RE.search(unit["text"])
                            else "mapped-publication-annotation",
                            target_id=block["id"],
                        )
    return applied_overrides


def _finish_ledger(
    units: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for unit in units:
        if unit["id"] in dispositions:
            continue
        text = unit["text"].strip()
        if not text:
            _dispose(dispositions, unit, "ignored-layout", reason="blank line")
        elif text == ">":
            _dispose(dispositions, unit, "ignored-quote-separator", reason="Markdown quote separator")
        else:
            _dispose(
                dispositions,
                unit,
                "unresolved-mapping",
                reason="meaningful legacy content was not classified",
            )
            findings.append(
                {"code": "unresolved-mapping", "rawUnitId": unit["id"], "line": unit["line"], "text": text}
            )
    return [dispositions[unit["id"]] for unit in units]


def _register_annotations(registry: dict[str, Any], document: dict[str, Any]) -> int:
    existing = {entry["id"] for entry in registry["entries"]}
    added = 0
    for kind, node in _annotation_nodes(document):
        if node["id"] in existing:
            continue
        label = node.get("text", {}).get("en", node["id"])
        allocate_registry_id(
            registry,
            entry_id=node["id"],
            kind=kind,
            canonical_key=f"legacy-aipg:{node['id']}",
            version="aipg-legacy-full-p4",
            label=label[:120],
        )
        if kind == "publication-annotation-block":
            registry["entries"][-1]["evidence"] = {
                "englishHash": sha256_text(node["text"]["en"]),
                "legacyRawUnits": node["sourceRawUnits"],
            }
        existing.add(node["id"])
        added += 1
    return added


def split_document(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    node_section = {}
    for section in document["sections"]:
        for _, node in walk_nodes([{"sections": [section]}]):
            node_section[node["id"]] = section["number"]
    result = {}
    for filename, document_id, numbers in DOCUMENT_LAYOUT:
        sections = [copy.deepcopy(section) for section in document["sections"] if section["number"] in numbers]
        annotations = [
            copy.deepcopy(annotation)
            for annotation in document["publicationAnnotations"]
            if node_section.get(annotation["anchor"]["id"]) in numbers
        ]
        result[filename] = {
            "schemaVersion": 1,
            "documentId": document_id,
            "sections": sections,
            "publicationAnnotations": annotations,
        }
    return result


def migrate_full(
    parsed: dict[str, Any],
    legacy_path: Path,
    registry: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    units, legacy_hash = tokenize_legacy(legacy_path)
    document = copy.deepcopy(parsed["document"])
    registry = copy.deepcopy(registry)
    dispositions: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    units_by_id = {unit["id"]: unit for unit in units}

    _consume_existing_provenance(document, units_by_id, dispositions)
    _mark_titles(document, units, dispositions)
    _map_body(document, units, dispositions, overrides, findings, mappings)
    applied_overrides = _apply_legacy_context_overrides(units, dispositions, overrides)
    _map_appendix_a(next(section for section in document["sections"] if section["number"] == "A"), units, dispositions)
    _map_appendix_b(next(section for section in document["sections"] if section["number"] == "B"), units, dispositions, findings)
    _mark_structural_and_preamble(units, dispositions)
    applied_overrides.extend(_map_annotations(document, units, dispositions, overrides, findings))
    ledger = _finish_ledger(units, dispositions, findings)

    for section in document["sections"]:
        for block in _blocks(section):
            if not block["text"]["zh"] and not any(
                finding.get("code") == "missing-translation" and finding.get("targetId") == block["id"]
                for finding in findings
            ):
                findings.append(
                    {
                        "code": "missing-translation",
                        "targetId": block["id"],
                        "officialText": block["text"]["en"],
                        "searchRange": list(SECTION_RANGES[section["number"]]),
                        "reason": "no legacy Chinese translation exists",
                    }
                )

    registry_added = _register_annotations(registry, document)
    unresolved_codes = {"unresolved-mapping", "ambiguous-mapping", "unresolved-annotation-mapping", "ambiguous-annotation-block-kind"}
    return {
        "documents": split_document(document),
        "registry": registry,
        "rawUnits": units,
        "coverageLedger": ledger,
        "blockMappings": mappings,
        "coverage": {
            "legacySha256": legacy_hash,
            "rawUnitCount": len(units),
            "disposedUnitCount": len(ledger),
            "duplicateConsumption": 0,
            "dispositions": dict(sorted(Counter(item["disposition"] for item in ledger).items())),
        },
        "findings": findings,
        "findingCounts": dict(sorted(Counter(item["code"] for item in findings).items())),
        "unresolvedCount": sum(finding["code"] in unresolved_codes for finding in findings),
        "registryAdded": registry_added,
        "appliedOverrides": sorted(applied_overrides),
    }
