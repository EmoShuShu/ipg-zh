"""Reader format follows mtr-zh, while IPG's native tree stays unchanged."""
from __future__ import annotations

import copy
import json

import pytest

from ipg_pipeline.builder import _annotation_markdown, _markdown_anchor, render_markdown, build_outputs
from ipg_pipeline.core import ROOT, load_yaml
from ipg_pipeline.reading import reading_events
from ipg_pipeline.release import current_release_dir


@pytest.fixture(scope="module")
def inputs():
    release = current_release_dir(ROOT)
    manifest = load_yaml(release / "manifest.yaml")
    documents = [load_yaml(release / name) for name in manifest["documents"]]
    display = load_yaml(ROOT / "src/ipg/display-values.yaml")
    return manifest, documents, display


def test_mtr_style_toc_bilingual_headings_and_levels(inputs):
    manifest, documents, display = inputs
    markdown = render_markdown(manifest, documents, display, candidate=False)
    assert markdown.index("# 版本说明") < markdown.index("# 目录") < markdown.index("# Magic: The Gathering Infraction Procedure Guide 万智牌违规处理方针")
    assert "版本：" + manifest["releaseId"] not in markdown
    lines = markdown.splitlines()
    toc = lines[lines.index("# 目录") + 1:lines.index("# Magic: The Gathering Infraction Procedure Guide 万智牌违规处理方针")]
    headings = [line for line in lines if line.startswith(("# ", "## "))]
    for document in documents:
        for section in document["sections"]:
            assert not any(line.startswith("# introduction ") or line.startswith("# framework ") for line in headings)
            level = "##" if section["kind"] in {"policy", "infraction"} else "#"
            # Look up actual text rather than freezing user-editable Chinese.
            heading = next(line for line in headings if section["title"]["en"] in line and section["title"]["zh"] in line)
            assert heading.startswith(level + " ")
            label = heading[len(level) + 1:]
            indent = "  " if level == "##" else ""
            assert f"{indent}- [{label}](#{_markdown_anchor(label)})" in toc
    assert len([line for line in toc if line.lstrip().startswith("- [")]) == 38


@pytest.mark.parametrize("heading,expected", [
    ("IPG 2.5 Game Rule Violation 违反游戏规则", "ipg-25-game-rule-violation-违反游戏规则"),
    ("Appendix A—Penalty Quick Reference 处罚快速查询", "appendix-apenalty-quick-reference-处罚快速查询"),
    ("[CANDIDATE 缺译] 中文/English (Test)", "candidate-缺译-中文english-test"),
])
def test_anchor_uses_mtr_unicode_and_punctuation_rules(heading, expected):
    assert _markdown_anchor(heading) == expected


def test_both_languages_follow_exact_reading_order_without_losing_annotation_blocks(inputs):
    manifest, documents, display = inputs
    before = copy.deepcopy(documents)
    markdown = render_markdown(manifest, documents, display, candidate=False)
    cursor = 0
    for document in documents:
        for kind, node, _ in reading_events(document):
            if kind in {"block", "reading-segment"}:
                for language in ("en", "zh"):
                    text = node["text"][language]
                    if text:
                        cursor = markdown.index(text, cursor) + len(text)
            elif kind == "annotation":
                for language in ("en", "zh"):
                    for group in node["groups"]:
                        for block in group["blocks"]:
                            quoted = "\n".join(">" if not line else ">" + line for line in block["text"][language].splitlines())
                            # List markers belong inside the quote, ahead of text.
                            if group["kind"] in {"ordered-list", "unordered-list"}:
                                quoted = quoted[1:]
                            if quoted:
                                cursor = markdown.index(quoted, cursor) + len(quoted)
    assert "> **AIPG 注解**" not in markdown
    assert documents == before


def test_annotation_multiline_paragraphs_and_lists_stay_in_same_bilingual_quote():
    annotation = {"groups": [
        {"kind": "paragraphs", "blocks": [{"text": {"en": "First\ncontinuation", "zh": "第一段\n续行"}}, {"text": {"en": "Second", "zh": "第二段"}}]},
        {"kind": "unordered-list", "blocks": [{"text": {"en": "One", "zh": "一"}}, {"text": {"en": "Two", "zh": "二"}}]},
        {"kind": "ordered-list", "blocks": [{"text": {"en": "Ordered", "zh": "有序"}}]},
    ]}
    assert "\n".join(_annotation_markdown(annotation)) == (
        ">First\n>continuation\n>\n>Second\n>\n>- One\n>- Two\n>\n>1. Ordered\n>\n"
        ">第一段\n>续行\n>\n>第二段\n>\n>- 一\n>- 二\n>\n>1. 有序\n"
    )


@pytest.mark.parametrize("marker", ["Z", "Y", "7"])
def test_annotation_preserves_persisted_list_markers_in_both_languages(marker):
    annotation = {"groups": [{"kind": "ordered-list", "blocks": [
        {"marker": marker, "text": {"en": "Original item", "zh": "原条目"}},
    ]}]}
    assert _annotation_markdown(annotation) == [
        f">{marker}. Original item", ">", f">{marker}. 原条目", "",
    ]


def test_missing_translation_marks_chinese_without_duplicating_english(inputs):
    manifest, documents, display = inputs
    document = copy.deepcopy(documents[0])
    block = document["sections"][0]["components"][0]["groups"][0]["blocks"][0]
    block["text"] = {"en": "UNIQUE OFFICIAL SOURCE", "zh": ""}
    markdown = render_markdown(manifest, [document], display, candidate=True)
    assert "UNIQUE OFFICIAL SOURCE\n\n**[CANDIDATE 缺译]**" in markdown
    assert markdown.count("UNIQUE OFFICIAL SOURCE") == 1
    assert block["text"]["zh"] == ""


def test_bilingual_appendices_keep_dates_entries_penalties_and_native_json(inputs, tmp_path):
    manifest, documents, display = inputs
    before = copy.deepcopy(documents)
    build_outputs(tmp_path, manifest, documents, display, candidate=False, profile="release")
    markdown = (tmp_path / "IPG.md").read_text(encoding="utf-8")
    rules = json.loads((tmp_path / "rules.json").read_text(encoding="utf-8"))
    appendix = next(section for section in rules["sections"] if section["number"] == "B")
    groups = appendix["components"][0]["groups"]
    starts = [markdown.index("## " + group["date"] + "\n") for group in groups]
    assert starts == sorted(starts)
    assert [len(group["blocks"]) for group in groups] == [5, 6, 1, 2, 3]
    for index, group in enumerate(groups):
        part = markdown[starts[index]:starts[index + 1] if index + 1 < len(starts) else len(markdown)]
        assert sum(line.startswith("- ") for line in part.splitlines()) == 2 * len(group["blocks"])
    for document in documents:
        for kind, node, _ in reading_events(document):
            if kind == "block" and node["type"] == "appendix-row":
                penalty = display["values"][node["displayCode"]]
                for language in ("en", "zh"):
                    assert f"{node['text'][language]} — {penalty[language]}" in markdown
    assert "### Penalty Quick Reference" not in markdown
    assert "### Changes from Previous Versions" not in markdown
    assert rules["sections"] == [section for doc in documents for section in doc["sections"]]
    assert rules["publicationAnnotations"] == [a for doc in documents for a in doc["publicationAnnotations"]]
    assert documents == before
