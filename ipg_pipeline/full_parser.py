from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import pdfplumber

from .core import sha256_bytes
from .pilot_parser import OFFICIAL_SHA256


PENALTIES = {
    "No Penalty": "penalty.none",
    "None": "penalty.none",
    "Warning": "penalty.warning",
    "Game Loss": "penalty.game-loss",
    "Match Loss": "penalty.match-loss",
    "Disqualification": "penalty.disqualification",
}
CLASSIFICATIONS = {
    "Game Play Error": "game-play-error",
    "Tournament Error": "tournament-error",
    "Unsporting Conduct": "unsporting-conduct",
}
ROLE_HEADINGS = {
    "Definition": ("definition", "component.definition"),
    "Examples": ("examples", "component.examples"),
    "Philosophy": ("philosophy", "component.philosophy"),
    "Additional Remedy": ("additional-remedy", "component.additional-remedy"),
}
DATE_RE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
TOC_RE = re.compile(r"^(.*?)\s*\.{3,}\s*(\d+)$")
MAIN_HEADING_RE = re.compile(r"^([1-4](?:\.\d+)?)\.\s+(.+)$")
LIST_RE = re.compile(r"^([A-Z])\.\s+(.*)$")
RULE_REF_RE = re.compile(r"\b([1-4](?:\.\d+)?)\s*:")
EXPECTED_TOC_KEYS = {
    "introduction", "framework", "1", "1.1", "1.2", "1.3", "1.4", "1.5",
    "2", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6",
    "3", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "3.9",
    "4", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "A", "B",
}


class FullParseError(ValueError):
    def __init__(self, findings: list[dict[str, Any]]):
        self.findings = findings
        super().__init__("; ".join(finding["code"] for finding in findings))


def _bbox(lines: list[dict[str, Any]]) -> list[float]:
    return [
        round(min(line["x0"] for line in lines), 2),
        round(min(line["top"] for line in lines), 2),
        round(max(line["x1"] for line in lines), 2),
        round(max(line["bottom"] for line in lines), 2),
    ]


def _provenance(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for page in dict.fromkeys(line["page"] for line in lines):
        page_lines = [line for line in lines if line["page"] == page]
        result.append(
            {
                "pdfSha256": OFFICIAL_SHA256,
                "page": page,
                "bbox": _bbox(page_lines),
                "extractionUnit": f"{page_lines[0]['id']}..{page_lines[-1]['id']}",
            }
        )
    return result


def extract_full_pdf(pdf_path: Path) -> dict[str, Any]:
    data = pdf_path.read_bytes()
    digest = sha256_bytes(data)
    if digest != OFFICIAL_SHA256:
        raise FullParseError([{"code": "pdf-hash-mismatch", "expected": OFFICIAL_SHA256, "actual": digest}])
    lines: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) != 31:
            raise FullParseError([{"code": "pdf-page-count", "expected": 31, "actual": len(pdf.pages)}])
        for page_number, page in enumerate(pdf.pages, 1):
            for index, line in enumerate(page.extract_text_lines(strip=True, return_chars=True), 1):
                chars = line.get("chars", [])
                lines.append(
                    {
                        "id": f"extract-p{page_number:02d}-l{index:03d}",
                        "page": page_number,
                        "text": line["text"].strip(),
                        "x0": round(line["x0"], 2),
                        "top": round(line["top"], 2),
                        "x1": round(line["x1"], 2),
                        "bottom": round(line["bottom"], 2),
                        "bold": bool(
                            chars
                            and all(
                                char.get("text", "").isspace()
                                or "Bold" in char.get("fontname", "")
                                for char in chars
                            )
                        ),
                    }
                )
    return {"schemaVersion": 1, "pdf": {"sha256": digest, "pages": 31}, "lines": lines}


def _toc_key(title: str) -> tuple[str, str, str]:
    if title == "Introduction":
        return "introduction", title, "front-matter"
    if title == "Framework of this Document":
        return "framework", title, "front-matter"
    appendix = re.match(r"^Appendix ([AB]) — (.+)$", title)
    if appendix:
        return appendix.group(1), appendix.group(2), "appendix"
    numeric = re.match(r"^([1-4](?:\.\d+)?)\.\s+(.+)$", title)
    if not numeric:
        raise ValueError(title)
    number, name = numeric.groups()
    kind = "infraction" if "." in number and number[0] in "234" else "policy" if "." in number else "chapter"
    return number, name, kind


def _parse_toc(lines: list[dict[str, Any]], mark) -> list[dict[str, Any]]:
    entries = []
    for line in lines:
        text = line["text"]
        if text == "CONTENTS":
            mark(line, "toc-heading")
            continue
        match = TOC_RE.match(text)
        if match:
            title, page = match.groups()
            key, name, kind = _toc_key(title)
            entries.append({"key": key, "title": name, "tocTitle": title, "kind": kind, "page": int(page), "lineId": line["id"]})
            mark(line, "toc-entry")
            continue
        if text == "2" and line["top"] > 720:
            mark(line, "page-number")
            continue
        mark(line, "unclassified-toc")
    return entries


def _join(lines: list[dict[str, Any]]) -> str:
    text = ""
    for line in lines:
        part = line.get("content", line["text"])
        if text.endswith("-"):
            text += part
        else:
            text += (" " if text else "") + part
    return text


def _make_groups(lines: list[dict[str, Any]], temp, mark) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        source_lines = current["lines"]
        block = {
            "id": temp("block"),
            "type": current["type"],
            "text": {"en": _join(source_lines), "zh": ""},
            "officialPdfUnits": _provenance(source_lines),
        }
        if current.get("marker"):
            block["marker"] = current["marker"]
        blocks.append(block)
        for line in source_lines:
            mark(line, "official-block-text", block["id"])
        current = None

    for line in lines:
        text = line.get("content", line["text"])
        marker = None
        block_type = "paragraph"
        content = text
        ordered = LIST_RE.match(text)
        if text.startswith("• "):
            marker, content, block_type = "bullet", text[2:], "list-item"
        elif ordered:
            marker, content, block_type = ordered.group(1), ordered.group(2), "list-item"
        prior = current["lines"][-1] if current else None
        same_page = bool(prior and line["page"] == prior["page"] and line["top"] - prior["bottom"] < 8)
        cross_page = bool(
            prior
            and line["page"] == prior["page"] + 1
            and not re.search(r"[.!?\u201d)]$", prior.get("content", prior["text"]))
            and content[:1].islower()
        )
        if current and marker is None and (same_page or cross_page):
            current["lines"].append({**line, "content": content})
        else:
            finish()
            current = {"type": block_type, "marker": marker, "lines": [{**line, "content": content}]}
    finish()

    groups: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []
    run_kind = ""
    for block in blocks:
        kind = "unordered-list" if block.get("marker") == "bullet" else "ordered-list" if block.get("marker", "").isalpha() else "paragraphs"
        if run and kind != run_kind:
            groups.append({"id": temp("group"), "kind": run_kind, "blocks": run})
            run = []
        run_kind = kind
        run.append(block)
    if run:
        groups.append({"id": temp("group"), "kind": run_kind, "blocks": run})
    return groups


def _split_infraction_heading(number: str, text: str) -> tuple[str, str, str] | None:
    for penalty in sorted(PENALTIES, key=len, reverse=True):
        suffix = f" {penalty}"
        if text.endswith(suffix):
            without_penalty = text[: -len(suffix)]
            for label, classification in CLASSIFICATIONS.items():
                prefix = f"{label} — "
                if without_penalty.startswith(prefix):
                    return without_penalty[len(prefix) :], classification, PENALTIES[penalty]
    return None


def _parse_main(lines: list[dict[str, Any]], toc: dict[str, dict[str, Any]], temp, mark, findings) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    component_lines: list[dict[str, Any]] = []
    role = "body"
    label_code = "component.body"

    def finish_component() -> None:
        nonlocal component_lines
        if current is not None and component_lines:
            current["components"].append(
                {"id": temp("component"), "role": role, "labelCode": label_code, "groups": _make_groups(component_lines, temp, mark)}
            )
        component_lines = []

    def finish_section() -> None:
        nonlocal current
        finish_component()
        if current is not None:
            sections.append(current)
        current = None

    seen: Counter[str] = Counter()

    def check_toc_heading(entry: dict[str, Any] | None, *, key: str, title: str, line: dict[str, Any]) -> None:
        if entry is None:
            return
        normalized = lambda value: " ".join(value.casefold().split())
        if normalized(entry["title"]) != normalized(title):
            findings.append(
                {"code": "toc-body-title-mismatch", "key": key, "tocTitle": entry["title"], "bodyTitle": title}
            )
        if entry["page"] != line["page"]:
            findings.append(
                {"code": "toc-body-page-mismatch", "key": key, "tocPage": entry["page"], "bodyPage": line["page"]}
            )

    for line in lines:
        text = line["text"]
        if text == str(line["page"]) and line["top"] > 720:
            mark(line, "page-number")
            continue
        heading = MAIN_HEADING_RE.match(text)
        if heading:
            number, heading_text = heading.groups()
            if number in {"1", "2", "3", "4"}:
                finish_section()
                entry = toc.get(number)
                if entry is None:
                    findings.append({"code": "body-heading-missing-from-toc", "key": number, "lineId": line["id"]})
                    title = heading_text.title()
                else:
                    title = entry["title"]
                check_toc_heading(entry, key=number, title=heading_text, line=line)
                current = {"id": temp("section"), "kind": "chapter", "number": number, "title": {"en": title, "zh": ""}, "components": []}
                role, label_code = "body", "component.body"
                seen[number] += 1
                mark(line, "section-heading", current["id"])
                continue
            finish_section()
            entry = toc.get(number)
            if entry is None:
                findings.append({"code": "body-heading-missing-from-toc", "key": number, "lineId": line["id"]})
            if number.startswith(("2.", "3.", "4.")):
                parsed = _split_infraction_heading(number, heading_text)
                if parsed is None:
                    findings.append({"code": "invalid-infraction-heading-or-penalty", "key": number, "text": text, "lineId": line["id"]})
                    title, classification, penalty = heading_text, None, None
                else:
                    title, classification, penalty = parsed
                toc_title = (
                    f"{next(label for label, value in CLASSIFICATIONS.items() if value == classification)} — {title}"
                    if classification
                    else title
                )
                check_toc_heading(entry, key=number, title=toc_title, line=line)
                current = {"id": temp("section"), "kind": "infraction", "number": number, "title": {"en": title, "zh": ""}, "components": []}
                if classification:
                    current["classification"] = classification
                if penalty:
                    current["penaltyCode"] = penalty
            else:
                check_toc_heading(entry, key=number, title=heading_text, line=line)
                current = {"id": temp("section"), "kind": "policy", "number": number, "title": {"en": entry["title"] if entry else heading_text.title(), "zh": ""}, "components": []}
            role, label_code = "body", "component.body"
            seen[number] += 1
            mark(line, "section-heading", current["id"])
            continue
        if current is None:
            mark(line, "unclassified-body")
            continue
        if text in ROLE_HEADINGS:
            finish_component()
            role, label_code = ROLE_HEADINGS[text]
            mark(line, "component-heading")
            continue
        if current["number"] == "1.1" and text in {"Warning", "Game Loss", "Match Loss", "Disqualification"}:
            finish_component()
            role, label_code = "penalty-definition", PENALTIES[text]
            mark(line, "penalty-definition-heading")
            continue
        prefixed_role = next((item for item in ("Upgrade", "Downgrade") if text.startswith(f"{item}:")), None)
        if prefixed_role:
            finish_component()
            role = prefixed_role.lower()
            label_code = f"component.{role}"
            component_lines.append({**line, "content": text.split(":", 1)[1].strip()})
            continue
        if line["bold"]:
            findings.append({"code": "unrecognized-role-heading", "text": text, "lineId": line["id"]})
            mark(line, "unclassified-body")
            continue
        component_lines.append(line)
    finish_section()
    for key, count in sorted(seen.items()):
        if count > 1:
            findings.append({"code": "duplicate-body-section", "key": key, "count": count})
    return sections


def _parse_front(lines: list[dict[str, Any]], temp, mark, findings) -> list[dict[str, Any]]:
    sections = []
    current = None
    body: list[dict[str, Any]] = []

    def finish() -> None:
        nonlocal current, body
        if current is not None:
            current["components"] = [{"id": temp("component"), "role": "body", "labelCode": "component.body", "groups": _make_groups(body, temp, mark)}]
            sections.append(current)
        current, body = None, []

    for line in lines:
        text = line["text"]
        if text in {"MAGIC INFRACTION PROCEDURE GUIDE", "Effective September 23, 2024"}:
            mark(line, "document-metadata")
        elif text == "INTRODUCTION":
            finish(); current = {"id": temp("section"), "kind": "front-matter", "number": "introduction", "title": {"en": "Introduction", "zh": ""}, "components": []}; mark(line, "section-heading", current["id"])
        elif text == "FRAMEWORK OF THIS DOCUMENT":
            finish(); current = {"id": temp("section"), "kind": "front-matter", "number": "framework", "title": {"en": "Framework of this Document", "zh": ""}, "components": []}; mark(line, "section-heading", current["id"])
        elif text == "1" and line["top"] > 720:
            mark(line, "page-number")
        elif current is None:
            findings.append({"code": "unclassified-front-matter", "lineId": line["id"], "text": text}); mark(line, "unclassified-body")
        else:
            body.append(line)
    finish()
    return sections


def _parse_appendix_a(lines: list[dict[str, Any]], infraction_by_title: dict[str, dict[str, Any]], temp, mark, findings) -> dict[str, Any]:
    blocks = []
    heading_count = 0
    for line in lines:
        text = line["text"]
        if text == "APPENDIX A — PENALTY QUICK REFERENCE":
            heading_count += 1
            mark(line, "section-heading")
        elif text in {"Infraction Penalty", "Game Play Errors", "Tournament Errors", "Unsporting Conduct"}:
            mark(line, "appendix-table-heading")
        elif text == "30" and line["top"] > 720:
            mark(line, "page-number")
        else:
            penalty = next((name for name in sorted(PENALTIES, key=len, reverse=True) if text.endswith(f" {name}")), None)
            if penalty is None:
                findings.append({"code": "unclassified-appendix-a", "lineId": line["id"], "text": text}); mark(line, "unclassified-body"); continue
            title = text[: -(len(penalty) + 1)]
            lookup = title.casefold().removeprefix("unsporting conduct — ")
            section = infraction_by_title.get(lookup)
            if section is None:
                findings.append({"code": "appendix-a-unknown-infraction", "lineId": line["id"], "title": title})
                reference = temp("unresolved")
            else:
                reference = section["id"]
                if section.get("penaltyCode") != PENALTIES[penalty]:
                    findings.append({"code": "appendix-a-penalty-mismatch", "section": section["number"], "appendixPenalty": PENALTIES[penalty], "bodyPenalty": section.get("penaltyCode")})
            block = {"id": temp("block"), "type": "appendix-row", "text": {"en": title, "zh": ""}, "displayCode": PENALTIES[penalty], "referenceId": reference, "officialPdfUnits": _provenance([line])}
            blocks.append(block); mark(line, "appendix-a-row", block["id"])
    if heading_count != 1:
        findings.append({"code": "appendix-heading-count", "appendix": "A", "count": heading_count})
    return {"id": temp("section"), "kind": "appendix", "number": "A", "title": {"en": "Penalty Quick Reference", "zh": ""}, "components": [{"id": temp("component"), "role": "appendix-table", "labelCode": "component.appendix-table", "groups": [{"id": temp("group"), "kind": "appendix-table", "blocks": blocks}]}]}


def _parse_appendix_b(lines: list[dict[str, Any]], known_numbers: set[str], temp, mark, findings) -> dict[str, Any]:
    groups = []
    current_date = None
    entries: list[dict[str, Any]] = []
    current_lines: list[dict[str, Any]] = []
    heading_count = 0

    def finish_entry() -> None:
        nonlocal current_lines
        if not current_lines:
            return
        text = _join(current_lines)
        refs = list(dict.fromkeys(RULE_REF_RE.findall(text)))
        for reference in refs:
            if reference not in known_numbers:
                findings.append({"code": "appendix-b-unknown-reference", "reference": reference, "lineId": current_lines[0]["id"]})
        block = {"id": temp("block"), "type": "change-entry", "text": {"en": text, "zh": ""}, "ruleReferences": refs, "officialPdfUnits": _provenance(current_lines)}
        entries.append(block)
        for line in current_lines:
            mark(line, "appendix-b-entry", block["id"])
        current_lines = []

    def finish_group() -> None:
        nonlocal entries
        finish_entry()
        if current_date is not None:
            groups.append({"id": temp("group"), "kind": "change-list", "date": current_date, "blocks": entries})
        entries = []

    for line in lines:
        text = line["text"]
        if text == "APPENDIX B — CHANGES FROM PREVIOUS VERSIONS":
            heading_count += 1
            mark(line, "section-heading")
        elif DATE_RE.match(text):
            finish_group(); current_date = text; mark(line, "appendix-b-date")
        elif re.match(r"^[1-4](?:\.\d+)?:", text):
            finish_entry(); current_lines = [line]
        elif text == "31" and line["top"] > 720:
            mark(line, "page-number")
        elif text.startswith("All trademarks are property"):
            finish_entry(); mark(line, "document-footer")
        elif current_lines:
            current_lines.append(line)
        else:
            findings.append({"code": "unclassified-appendix-b", "lineId": line["id"], "text": text}); mark(line, "unclassified-body")
    finish_group()
    if heading_count != 1:
        findings.append({"code": "appendix-heading-count", "appendix": "B", "count": heading_count})
    return {"id": temp("section"), "kind": "appendix", "number": "B", "title": {"en": "Changes from Previous Versions", "zh": ""}, "components": [{"id": temp("component"), "role": "change-log", "labelCode": "component.change-log", "groups": groups}]}


def parse_full_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if extraction.get("pdf", {}).get("sha256") != OFFICIAL_SHA256:
        raise FullParseError([{"code": "pdf-hash-mismatch", "expected": OFFICIAL_SHA256, "actual": extraction.get("pdf", {}).get("sha256")}])
    counters: Counter[str] = Counter()
    dispositions: dict[str, dict[str, Any]] = {}

    def temp(kind: str) -> str:
        counters[kind] += 1
        return f"extract-{kind}-{counters[kind]:04d}"

    def mark(line: dict[str, Any], disposition: str, target: str | None = None) -> None:
        if line["id"] in dispositions:
            raise FullParseError([{"code": "duplicate-line-consumption", "lineId": line["id"]}])
        dispositions[line["id"]] = {"lineId": line["id"], "page": line["page"], "text": line["text"], "disposition": disposition, "targetId": target}

    by_page = {page: [line for line in extraction["lines"] if line["page"] == page] for page in range(1, 32)}
    toc_entries = _parse_toc(by_page[2], mark)
    toc_counts = Counter(item["key"] for item in toc_entries)
    for key, count in toc_counts.items():
        if count > 1:
            findings.append({"code": "duplicate-toc-section", "key": key, "count": count})
    toc = {item["key"]: item for item in toc_entries}
    for key in sorted(EXPECTED_TOC_KEYS - set(toc)):
        findings.append({"code": "toc-missing-expected-section", "key": key})
    for key in sorted(set(toc) - EXPECTED_TOC_KEYS):
        findings.append({"code": "toc-unexpected-section", "key": key})
    front = _parse_front(by_page[1], temp, mark, findings)
    main = _parse_main([line for page in range(3, 30) for line in by_page[page]], toc, temp, mark, findings)
    body_keys = [section["number"] for section in [*front, *main]]
    for key, count in Counter(body_keys).items():
        if count > 1:
            findings.append({"code": "duplicate-body-section", "key": key, "count": count})
    for key in sorted(set(toc) - set(body_keys) - {"A", "B"}):
        findings.append({"code": "toc-section-missing-from-body", "key": key})
    for key in sorted(set(body_keys) - set(toc)):
        findings.append({"code": "body-heading-missing-from-toc", "key": key})
    toc_body_order = [item["key"] for item in toc_entries if item["key"] not in {"A", "B"}]
    if body_keys != toc_body_order:
        findings.append({"code": "toc-body-order-mismatch", "toc": toc_body_order, "body": body_keys})
    for section in front:
        entry = toc.get(section["number"])
        if entry and (entry["title"] != section["title"]["en"] or entry["page"] != 1):
            findings.append({"code": "toc-body-front-matter-mismatch", "key": section["number"]})
    infractions = [section for section in main if section["kind"] == "infraction"]
    if len(infractions) != 23:
        findings.append({"code": "infraction-count-cross-check", "expected": 23, "actual": len(infractions)})
    for section in infractions:
        if not section.get("penaltyCode"):
            findings.append({"code": "infraction-missing-base-penalty", "section": section["number"]})
    infraction_by_title = {section["title"]["en"].casefold(): section for section in infractions}
    appendix_a = _parse_appendix_a(by_page[30], infraction_by_title, temp, mark, findings)
    appendix_b = _parse_appendix_b(by_page[31], {section["number"] for section in [*front, *main]}, temp, mark, findings)
    if "A" not in toc:
        findings.append({"code": "body-heading-missing-from-toc", "key": "A"})
    if "B" not in toc:
        findings.append({"code": "body-heading-missing-from-toc", "key": "B"})
    for key, page, title in (("A", 30, appendix_a["title"]["en"]), ("B", 31, appendix_b["title"]["en"])):
        entry = toc.get(key)
        if entry and (entry["title"] != title or entry["page"] != page):
            findings.append({"code": "toc-body-appendix-mismatch", "key": key})

    unclassified = [line for line in extraction["lines"] if line["id"] not in dispositions]
    for line in unclassified:
        findings.append({"code": "unclassified-pdf-text", "lineId": line["id"], "page": line["page"], "text": line["text"]})
    explicitly_unclassified = [item for item in dispositions.values() if item["disposition"].startswith("unclassified")]
    if explicitly_unclassified:
        findings.append({"code": "unclassified-body-candidates", "count": len(explicitly_unclassified), "lineIds": [item["lineId"] for item in explicitly_unclassified]})
    if findings:
        raise FullParseError(findings)

    document = {"schemaVersion": 1, "documentId": "extract-ipg-full-official", "sections": [*front, *main, appendix_a, appendix_b], "publicationAnnotations": []}
    ordered_coverage = [dispositions[line["id"]] for line in extraction["lines"]]
    return {
        "schemaVersion": 1,
        "idStatus": "temporary-extraction-ids",
        "pdf": extraction["pdf"],
        "toc": toc_entries,
        "document": document,
        "coverage": {
            "lineCount": len(extraction["lines"]),
            "classifiedLineCount": len(ordered_coverage),
            "unclassifiedLineCount": 0,
            "dispositions": dict(sorted(Counter(item["disposition"] for item in ordered_coverage).items())),
            "lines": ordered_coverage,
        },
    }


def parse_full_pdf(pdf_path: Path) -> dict[str, Any]:
    return parse_full_extraction(extract_full_pdf(pdf_path))
