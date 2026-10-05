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
    version_notes: str | None = None,
) -> str:
    version_notes = read_version_notes() if version_notes is None else version_notes
    values = display_values["values"]
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
    lines.extend([version_notes, "", "# 万智牌违规处理方针", "", f"版本：{manifest['releaseId']}", ""])

    for document in documents:
        for kind, node, _ in reading_events(document):
            if kind == "section":
                section = node
                level = 2 if section["kind"] in {"front-matter", "chapter", "appendix"} else 3
                prefix = "" if section["kind"] == "front-matter" else f"{section['number']} "
                heading = f"{'#' * level} {prefix}{translated(section['title'])}"
                if section["title"]["zh"]:
                    heading += f" ({section['title']['en']})"
                lines.extend([heading, ""])
                if "penaltyCode" in section:
                    lines.extend([f"**处罚：{values[section['penaltyCode']]['zh']}**", ""])
            elif kind == "component":
                if node["role"] != "body" and section["kind"] != "appendix":
                    lines.extend([f"#### {values[node['labelCode']]['zh']}", ""])
            elif kind == "group" and "date" in node:
                lines.extend([f"### {node['date']}", ""])
            elif kind == "annotation":
                lines.extend(_annotation_markdown(node))
            elif kind in {"block", "reading-segment"}:
                value = translated(node["text"])
                marker = node.get("marker")
                if node["type"] == "appendix-row":
                    value = f"{value} — {values[node['displayCode']]['zh']}"
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
