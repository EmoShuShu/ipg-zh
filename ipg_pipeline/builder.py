from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import ROOT, sha256_bytes
from .reading import reading_events


VERSION_NOTES_PATH = Path("src/ipg/version-notes.md")


def read_version_notes(source_root: Path = ROOT) -> str:
    path = source_root / VERSION_NOTES_PATH
    value = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value.startswith("# 版本说明\n"):
        raise ValueError(f"版本说明必须以 '# 版本说明' 标题开头：{path}")
    if re.search(r"(?m)^# 目录\s*$", value):
        raise ValueError(f"版本说明不得包含目录：{path}")
    return value


def _translated(text: dict[str, str]) -> str:
    return text["zh"] or "**[CANDIDATE 缺译]**"


# The quote and heading-anchor helpers follow mtr-zh's Markdown builder.
# Keep them local: the IPG pipeline must never depend on the reference checkout.
def _quote_markdown(value: str) -> str:
    return "\n".join(">" if line == "" else f">{line}" for line in value.splitlines())


def _markdown_anchor(value: str) -> str:
    normalized = "".join(
        character for character in value.casefold()
        if character.isalnum() or character in {" ", "-", "_"}
    )
    return re.sub(r"\s", "-", normalized).strip("-")


def _section_heading(section: dict[str, Any]) -> str:
    title = f"{section['title']['en']} {_translated(section['title'])}"
    if section["kind"] == "front-matter":
        return title
    if section["kind"] == "appendix":
        return f"Appendix {section['number']}—{title}"
    number = section["number"] + ("." if section["kind"] == "chapter" else "")
    return f"IPG {number} {title}"


def _markdown_toc(documents: list[dict[str, Any]]) -> str:
    lines = ["# 目录", "", "- [版本说明](#版本说明)", "- [目录](#目录)"]
    for document in documents:
        for section in document["sections"]:
            heading = _section_heading(section)
            indent = "  " if section["kind"] in {"policy", "infraction"} else ""
            lines.append(f"{indent}- [{heading}](#{_markdown_anchor(heading)})")
    return "\n".join(lines)


def _annotation_markdown(annotation: dict[str, Any]) -> list[str]:
    # Match MTR extras: English and Chinese in one quote, preserving paragraphs
    # and lists. Annotations remain separate objects in IPG JSON/reading order.
    languages = []
    for language in ("en", "zh"):
        groups = []
        for group in annotation["groups"]:
            blocks = []
            for index, block in enumerate(group["blocks"], 1):
                value = block["text"]["en"] if language == "en" else _translated(block["text"])
                if group["kind"] == "unordered-list":
                    value = f"- {value}"
                elif group["kind"] == "ordered-list":
                    value = f"{block.get('marker', index)}. {value}"
                blocks.append(value)
            separator = "\n" if group["kind"] in {"unordered-list", "ordered-list"} else "\n\n"
            groups.append(separator.join(blocks))
        languages.append(_quote_markdown("\n\n".join(groups)))
    return [languages[0], ">", languages[1], ""]


def render_markdown(
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    display_values: dict[str, Any],
    *,
    candidate: bool,
    version_notes: str | None = None,
) -> str:
    version_notes = read_version_notes() if version_notes is None else version_notes
    values = display_values["values"]
    lines = ["<!-- CANDIDATE: NOT FOR RELEASE -->" if candidate else "<!-- LOCAL BUILD: NOT A PUBLICATION APPROVAL -->"]
    if candidate:
        full_scope = (
            manifest["scope"]["officialContent"]["mode"] == "full-document"
            and manifest["scope"]["publicationAnnotations"]["mode"] == "full-document"
        )
        lines.extend(
            [
                (
                    "> **全文迁移候选版：不得发布。** candidate 检查不代表已完成审校，请以 release 检查确认本地最终文件生成条件；公开发布许可需另行确认。"
                    if full_scope
                    else "> **候选试制版：不得发布。** 本文件范围尚不完整，且尚未完成完整审校。"
                ),
                "",
            ]
        )
    lines.extend([
        version_notes, "", _markdown_toc(documents), "",
        "# Magic: The Gathering Infraction Procedure Guide 万智牌违规处理方针", "",
    ])

    for document in documents:
        for kind, node, _ in reading_events(document):
            if kind == "section":
                section = node
                level = 1 if section["kind"] in {"front-matter", "chapter", "appendix"} else 2
                heading = f"{'#' * level} {_section_heading(section)}"
                lines.extend([heading, ""])
                if "penaltyCode" in section:
                    penalty = values[section["penaltyCode"]]
                    lines.extend([f"**Penalty: {penalty['en']} / 处罚：{_translated(penalty)}**", ""])
            elif kind == "component":
                if node["role"] != "body" and section["kind"] != "appendix":
                    label = values[node["labelCode"]]
                    lines.extend([f"### {label['en']} {_translated(label)}", ""])
            elif kind == "group" and "date" in node:
                lines.extend([f"## {node['date']}", ""])
            elif kind == "annotation":
                lines.extend(_annotation_markdown(node))
            elif kind in {"block", "reading-segment"}:
                for language in ("en", "zh"):
                    value = node["text"]["en"] if language == "en" else _translated(node["text"])
                    marker = node.get("marker")
                    if node["type"] == "appendix-row":
                        penalty = values[node["displayCode"]]
                        value = f"{value} — {penalty['en'] if language == 'en' else _translated(penalty)}"
                    if marker == "bullet" or node["type"] == "change-entry":
                        value = f"- {value}"
                    elif marker:
                        value = f"{marker}. {value}"
                    lines.extend([value, ""])
    return "\n".join(lines).rstrip() + "\n"


def build_outputs(
    output_dir: Path,
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    display_values: dict[str, Any],
    *,
    candidate: bool,
    profile: str,
    source_root: Path = ROOT,
) -> dict[str, Any]:
    version_notes = read_version_notes(source_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(
        manifest, documents, display_values, candidate=candidate, version_notes=version_notes
    )
    rules = {
        "schema": "ipg-output-v1",
        "releaseId": manifest["releaseId"],
        "versions": manifest["versions"],
        "scope": manifest["scope"],
        "publishable": manifest["publishable"],
        "candidate": candidate,
        "versionNotes": {"id": "ipg-version-notes", "en": "", "zh": version_notes},
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
