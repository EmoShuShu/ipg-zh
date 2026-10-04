from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pdfplumber

from .core import sha256_bytes


OFFICIAL_SHA256 = "056b1687bae4a6c5d7bc4fe4e15d66613e725197cc2a45e18084c2eaf1b5aae0"
SUPPORTED_PAGES = (*range(7, 15), 30, 31)
COMPONENTS = {
    "Definition": ("definition", "component.definition"),
    "Examples": ("examples", "component.examples"),
    "Philosophy": ("philosophy", "component.philosophy"),
    "Additional Remedy": ("additional-remedy", "component.additional-remedy"),
}
PENALTIES = {"No Penalty": "penalty.none", "Warning": "penalty.warning"}
SECTION_RE = re.compile(r"^(2\.\d+)\. Game Play Error — (.+?) (No Penalty|Warning)$")
EXAMPLE_RE = re.compile(r"^([A-Z])\.\s+(.*)$")


def _bbox(lines: list[dict[str, Any]]) -> list[float]:
    return [
        round(min(line["x0"] for line in lines), 2),
        round(min(line["top"] for line in lines), 2),
        round(max(line["x1"] for line in lines), 2),
        round(max(line["bottom"] for line in lines), 2),
    ]


def _provenance(page: int, lines: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    return {
        "pdfSha256": OFFICIAL_SHA256,
        "page": page,
        "bbox": _bbox(lines),
        "extractionUnit": unit,
    }


def _id_part(value: str) -> str:
    return value.replace(".", "-").lower()


def _extract_lines(pdf: pdfplumber.PDF, pages: Iterable[int]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for page_number in pages:
        if page_number not in SUPPORTED_PAGES:
            raise ValueError(f"page {page_number} is outside the approved pilot fixture")
        page = pdf.pages[page_number - 1]
        for line in page.extract_text_lines(strip=True, return_chars=False):
            text = line["text"].strip()
            if line["top"] > 720 and text == str(page_number):
                continue
            lines.append(
                {
                    "page": page_number,
                    "text": text,
                    "x0": round(line["x0"], 2),
                    "top": round(line["top"], 2),
                    "x1": round(line["x1"], 2),
                    "bottom": round(line["bottom"], 2),
                }
            )
    return lines


def _make_blocks(
    lines: list[dict[str, Any]], section_id: str, component_role: str
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    group_number = 1
    block_number = 0

    def finish() -> None:
        nonlocal current, group_number, block_number
        if current is None:
            return
        block_number += 1
        marker = current.pop("marker", None)
        block_type = current.pop("type")
        source_lines = current.pop("lines")
        text = " ".join(part["text"] for part in source_lines)
        block: dict[str, Any] = {
            "id": f"{section_id}-c-{component_role}-g{group_number:02d}-b{block_number:03d}",
            "type": block_type,
            "text": {"en": text, "zh": ""},
            "officialPdfUnit": _provenance(
                source_lines[0]["page"], source_lines, f"page-{source_lines[0]['page']}-line-block"
            ),
        }
        if marker:
            block["marker"] = marker
        blocks.append(block)
        current = None

    for line in lines:
        text = line["text"]
        marker = None
        block_type = "paragraph"
        content = text
        example = EXAMPLE_RE.match(text)
        if text.startswith("• "):
            marker, content, block_type = "bullet", text[2:], "list-item"
        elif example:
            marker, content, block_type = example.group(1), example.group(2), "list-item"

        starts_item = marker is not None
        continues = bool(
            current
            and not starts_item
            and line["page"] == current["lines"][-1]["page"]
            and line["top"] - current["lines"][-1]["bottom"] < 8
        )
        if continues:
            current["lines"].append({**line, "text": content})
            continue
        finish()
        current = {"type": block_type, "marker": marker, "lines": [{**line, "text": content}]}
    finish()
    return blocks


def _groups_for_component(lines: list[dict[str, Any]], section_id: str, role: str) -> list[dict[str, Any]]:
    blocks = _make_blocks(lines, section_id, role)
    groups: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []
    run_kind: str | None = None
    group_number = 0
    for block in blocks:
        kind = (
            "unordered-list"
            if block.get("marker") == "bullet"
            else "ordered-list"
            if block.get("marker", "").isalpha()
            else "paragraphs"
        )
        if run and kind != run_kind:
            group_number += 1
            group_id = f"{section_id}-c-{role}-g{group_number:02d}"
            for index, item in enumerate(run, 1):
                item["id"] = f"{group_id}-b{index:03d}"
            groups.append({"id": group_id, "kind": run_kind, "blocks": run})
            run = []
        run_kind = kind
        run.append(block)
    if run:
        group_number += 1
        group_id = f"{section_id}-c-{role}-g{group_number:02d}"
        for index, item in enumerate(run, 1):
            item["id"] = f"{group_id}-b{index:03d}"
        groups.append({"id": group_id, "kind": run_kind, "blocks": run})
    return groups


def _parse_chapter(lines: list[dict[str, Any]]) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_role = "body"
    component_lines: list[dict[str, Any]] = []

    def finish_component() -> None:
        nonlocal component_lines
        if current_section is None or not component_lines:
            component_lines = []
            return
        role = current_role
        current_section["components"].append(
            {
                "id": f"{current_section['id']}-c-{role}",
                "role": role,
                "labelCode": f"component.{role}",
                "groups": _groups_for_component(component_lines, current_section["id"], role),
            }
        )
        component_lines = []

    def finish_section() -> None:
        nonlocal current_section
        finish_component()
        if current_section is not None:
            sections.append(current_section)
        current_section = None

    for line in lines:
        text = line["text"]
        if text == "2. GAME PLAY ERRORS":
            finish_section()
            current_section = {
                "id": "ipg-s2",
                "kind": "chapter",
                "number": "2",
                "title": {"en": "Game Play Errors", "zh": ""},
                "components": [],
            }
            current_role = "body"
            continue
        match = SECTION_RE.match(text)
        if match:
            finish_section()
            number, title, penalty = match.groups()
            current_section = {
                "id": f"ipg-s{_id_part(number)}",
                "kind": "infraction",
                "number": number,
                "title": {"en": title, "zh": ""},
                "penaltyCode": PENALTIES[penalty],
                "components": [],
            }
            current_role = "body"
            continue
        if text in COMPONENTS:
            finish_component()
            current_role = COMPONENTS[text][0]
            continue
        if current_section is None:
            raise ValueError(f"unexpected Chapter 2 text before heading: {text}")
        component_lines.append(line)
    finish_section()
    if [section["number"] for section in sections] != ["2", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"]:
        raise ValueError("pilot Chapter 2 section boundaries changed")
    return {"schemaVersion": 1, "documentId": "ipg-chapter-02-pilot", "sections": sections, "publicationAnnotations": []}


def _parse_appendix_a(lines: list[dict[str, Any]]) -> dict[str, Any]:
    heading = lines.pop(0)
    if heading["text"] != "APPENDIX A — PENALTY QUICK REFERENCE":
        raise ValueError("Appendix A heading changed")
    rows: list[dict[str, Any]] = []
    penalty_names = ["No Penalty", "Warning", "Game Loss", "Match Loss", "Disqualification", "None"]
    for line in lines:
        text = line["text"]
        if text in {"Infraction Penalty", "Game Play Errors", "Tournament Errors", "Unsporting Conduct"}:
            continue
        penalty = next((name for name in penalty_names if text.endswith(f" {name}")), None)
        if penalty is None:
            continue
        infraction = text[: -(len(penalty) + 1)]
        code = "penalty.none" if penalty in {"No Penalty", "None"} else f"penalty.{penalty.lower().replace(' ', '-')}"
        rows.append(
            {
                "id": f"ipg-app-a-row-{len(rows)+1:02d}",
                "type": "appendix-row",
                "text": {"en": infraction, "zh": ""},
                "displayCode": code,
                "officialPdfUnit": _provenance(line["page"], [line], "appendix-a-row"),
            }
        )
    sample = [row for row in rows if row["text"]["en"] in {"Missed Trigger", "Game Rule Violation", "Tardiness", "Cheating"}]
    section = {
        "id": "ipg-app-a",
        "kind": "appendix",
        "number": "A",
        "title": {"en": "Penalty Quick Reference", "zh": ""},
        "components": [
            {
                "id": "ipg-app-a-c-table",
                "role": "appendix-table",
                "labelCode": "component.appendix-table",
                "groups": [{"id": "ipg-app-a-c-table-g01", "kind": "appendix-table", "blocks": sample}],
            }
        ],
    }
    return {"schemaVersion": 1, "documentId": "ipg-appendix-a-pilot", "sections": [section], "publicationAnnotations": []}


def _parse_appendix_b(lines: list[dict[str, Any]]) -> dict[str, Any]:
    heading = lines.pop(0)
    if heading["text"] != "APPENDIX B — CHANGES FROM PREVIOUS VERSIONS":
        raise ValueError("Appendix B heading changed")
    if not lines or lines[0]["text"] != "September 23, 2024":
        raise ValueError("Appendix B pilot version heading changed")
    lines.pop(0)
    entries: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for line in lines:
        if re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", line["text"]):
            break
        if re.match(r"^\d\.\d:", line["text"]):
            if current:
                entries.append(_appendix_change_block(current, len(entries) + 1))
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append(_appendix_change_block(current, len(entries) + 1))
    section = {
        "id": "ipg-app-b",
        "kind": "appendix",
        "number": "B",
        "title": {"en": "Changes from Previous Versions", "zh": ""},
        "components": [
            {
                "id": "ipg-app-b-c-change-log",
                "role": "change-log",
                "labelCode": "component.change-log",
                "groups": [{"id": "ipg-app-b-c-change-log-g01", "kind": "change-list", "blocks": entries}],
            }
        ],
    }
    return {"schemaVersion": 1, "documentId": "ipg-appendix-b-pilot", "sections": [section], "publicationAnnotations": []}


def _appendix_change_block(lines: list[dict[str, Any]], number: int) -> dict[str, Any]:
    return {
        "id": f"ipg-app-b-change-{number:02d}",
        "type": "change-entry",
        "text": {"en": " ".join(line["text"] for line in lines), "zh": ""},
        "officialPdfUnit": _provenance(lines[0]["page"], lines, "appendix-b-change-entry"),
    }


def parse_pilot(pdf_path: Path) -> dict[str, Any]:
    data = pdf_path.read_bytes()
    actual_hash = sha256_bytes(data)
    if actual_hash != OFFICIAL_SHA256:
        raise ValueError(f"official PDF hash mismatch: {actual_hash}")
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) != 31:
            raise ValueError(f"official PDF page count changed: {len(pdf.pages)}")
        chapter_lines = _extract_lines(pdf, range(7, 15))
        appendix_a_lines = _extract_lines(pdf, [30])
        appendix_b_lines = _extract_lines(pdf, [31])
    return {
        "schemaVersion": 1,
        "scope": "pilot-only-pages-7-14-30-31",
        "pdf": {"sha256": actual_hash, "pages": 31},
        "documents": {
            "chapter-02.yaml": _parse_chapter(chapter_lines),
            "appendix-a.yaml": _parse_appendix_a(appendix_a_lines),
            "appendix-b.yaml": _parse_appendix_b(appendix_b_lines),
        },
    }

