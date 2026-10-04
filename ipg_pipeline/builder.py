from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import sha256_bytes


def _annotation_markdown(annotation: dict[str, Any]) -> list[str]:
    lines = ["> **AIPG 注解**"]
    for group in annotation["groups"]:
        for index, block in enumerate(group["blocks"], 1):
            target = block["text"]["zh"] or f"**[CANDIDATE 缺译]** {block['text']['en']}"
            if group["kind"] == "unordered-list":
                lines.append(f"> - {target}")
            elif group["kind"] == "ordered-list":
                lines.append(f"> {index}. {target}")
            else:
                if len(lines) > 1:
                    lines.append(">")
                lines.append(f"> {target}")
    lines.append("")
    return lines


def render_markdown(
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    display_values: dict[str, Any],
    *,
    candidate: bool,
) -> str:
    values = display_values["values"]
    annotations: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        for annotation in document.get("publicationAnnotations", []):
            key = (annotation["anchor"]["type"], annotation["anchor"]["id"], annotation["position"])
            annotations[key].append(annotation)
    for anchored in annotations.values():
        anchored.sort(key=lambda item: item["order"])

    def add_annotations(lines: list[str], anchor_type: str, anchor_id: str, position: str) -> None:
        for annotation in annotations.get((anchor_type, anchor_id, position), []):
            lines.extend(_annotation_markdown(annotation))

    def translated(text: dict[str, str]) -> str:
        return text["zh"] or f"**[CANDIDATE 缺译]** {text['en']}"

    lines = ["<!-- CANDIDATE: NOT FOR RELEASE -->" if candidate else "<!-- RELEASE -->"]
    if candidate:
        full_scope = (
            manifest["scope"]["officialContent"]["mode"] == "full-document"
            and manifest["scope"]["publicationAnnotations"]["mode"] == "full-document"
        )
        lines.extend(
            [
                (
                    "> **全文迁移候选版：不得发布。** 本文件尚未完成完整审校，且仍有正式发布门槛。"
                    if full_scope
                    else "> **候选试制版：不得发布。** 本文件范围尚不完整，且尚未完成完整审校。"
                ),
                "",
            ]
        )
    lines.extend(["# 万智牌违规处理方针", "", f"版本：{manifest['releaseId']}", ""])

    for document in documents:
        for section in document["sections"]:
            add_annotations(lines, "section", section["id"], "before")
            top_level = section["kind"] in {"front-matter", "chapter", "appendix"}
            level = 2 if top_level else 3
            prefix = "" if section["kind"] == "front-matter" else f"{section['number']} "
            heading = f"{'#' * level} {prefix}{translated(section['title'])}"
            if section["title"]["zh"]:
                heading += f" ({section['title']['en']})"
            lines.extend([heading, ""])
            if "penaltyCode" in section:
                lines.extend([f"**处罚：{values[section['penaltyCode']]['zh']}**", ""])
            add_annotations(lines, "section", section["id"], "inside-start")
            for component in section["components"]:
                add_annotations(lines, "component", component["id"], "before")
                if component["role"] != "body" and section["kind"] != "appendix":
                    lines.extend([f"#### {values[component['labelCode']]['zh']}", ""])
                add_annotations(lines, "component", component["id"], "inside-start")
                for group in component["groups"]:
                    add_annotations(lines, "group", group["id"], "before")
                    add_annotations(lines, "group", group["id"], "inside-start")
                    if "date" in group:
                        lines.extend([f"### {group['date']}", ""])
                    for block in group["blocks"]:
                        add_annotations(lines, "block", block["id"], "before")
                        value = translated(block["text"])
                        marker = block.get("marker")
                        if block["type"] == "appendix-row":
                            value = f"{value} — {values[block['displayCode']]['zh']}"
                        if marker == "bullet":
                            lines.extend([f"- {value}", ""])
                        elif marker:
                            lines.extend([f"{marker}. {value}", ""])
                        elif block["type"] == "change-entry":
                            lines.extend([f"- {value}", ""])
                        else:
                            lines.extend([value, ""])
                        add_annotations(lines, "block", block["id"], "after")
                    add_annotations(lines, "group", group["id"], "inside-end")
                    add_annotations(lines, "group", group["id"], "after")
                add_annotations(lines, "component", component["id"], "inside-end")
                add_annotations(lines, "component", component["id"], "after")
            add_annotations(lines, "section", section["id"], "inside-end")
            add_annotations(lines, "section", section["id"], "after")
    return "\n".join(lines).rstrip() + "\n"


def build_outputs(
    output_dir: Path,
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    display_values: dict[str, Any],
    *,
    candidate: bool,
    profile: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(manifest, documents, display_values, candidate=candidate)
    rules = {
        "schema": "ipg-output-v1",
        "releaseId": manifest["releaseId"],
        "versions": manifest["versions"],
        "scope": manifest["scope"],
        "publishable": manifest["publishable"],
        "candidate": candidate,
        "displayValues": display_values["values"],
        "sections": [section for document in documents for section in document["sections"]],
        "publicationAnnotations": [
            annotation for document in documents for annotation in document.get("publicationAnnotations", [])
        ],
    }
    rules_text = json.dumps(rules, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    files = {"IPG.md": markdown.encode(), "rules.json": rules_text.encode()}
    for name, data in files.items():
        (output_dir / name).write_bytes(data)
    hashes = {name: sha256_bytes(data) for name, data in files.items()}
    sums = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    (output_dir / "SHA256SUMS").write_text(sums, encoding="ascii", newline="\n")
    report = {
        "schemaVersion": 1,
        "releaseId": manifest["releaseId"],
        "validationProfile": profile,
        "candidate": candidate,
        "outputs": {
            name: {"sha256": hashes[name], "bytes": len(files[name])} for name in sorted(files)
        },
        "rulesHashEmbedded": False,
    }
    (output_dir / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
