from __future__ import annotations

import os
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .core import ROOT
from . import full_review
from .production import build_project, describe_validation, validate_project
from .full_review import (
    CURRENT_CANDIDATE,
    MAPPING_PATH,
    PO_ORDER,
    PROJECT_DIR,
    PROGRESS_JSON,
    PROGRESS_MARKDOWN,
    TERMINOLOGY_JSON,
    TERMINOLOGY_MARKDOWN,
    _now,
    _write_json,
    apply_full_writeback,
    build_current_candidate,
    extract_project_translation_notes,
    initialize_full_review_state,
    inspect_target_project,
    prepare_full_project,
    preview_full_writeback,
    record_review_completion,
    refresh_progress_reports,
    rollback_applied_writeback,
    run_nonblocking_terminology_audit,
    validate_current_state,
)


PREVIEW_JSON = PROJECT_DIR / "import-preview.json"
PREVIEW_MARKDOWN = PROJECT_DIR / "import-preview.md"


def run_full_tests() -> dict[str, Any]:
    output = []
    with subprocess.Popen(
        [sys.executable, "-u", "-m", "pytest", "-o", "addopts=", "-v"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1"},
        encoding="utf-8",
        errors="replace",
    ) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            output.append(line)
        returncode = process.wait()
    captured = "".join(output).strip()
    if returncode:
        raise ValueError("完整测试失败：\n" + captured)
    return {"passed": True, "output": captured}


def _write_preview_markdown(preview: dict[str, Any]) -> None:
    lines = [
        "# OmegaT 回写预览",
        "",
        f"- 本批文件：{', '.join(preview['selectedPo'])}",
        f"- 已审单元：{preview['reviewedUnitCount']}",
        f"- 实际修改：{preview['actualChangeCount']}",
        f"- 同步汇总中文：{len(preview.get('derivedChanges', []))}",
        f"- 确认保留旧译：{preview['reviewedUnchangedCount']}",
        f"- 含句段批注的单元：{preview['noteUnitCount']}",
        "",
        "## 修改明细",
        "",
    ]
    if not preview["changes"]:
        lines.append("本批没有文字修改；所选文件仍可登记为 reviewed-unchanged。")
    for change in preview["changes"]:
        lines.extend(
            [
                f"### {change['unitId']}",
                "",
                f"- 文件：{change['file']}",
                f"- 原译：{change['oldTarget']}",
                f"- 新译：{change['newTarget']}",
                "",
            ]
        )
    for change in preview.get("derivedChanges", []):
        lines.extend(["### 同步父段落汇总中文", "", f"- 文件：{change['file']}",
                      f"- 原译：{change['oldTarget']}", f"- 新译：{change['newTarget']}", ""])
    PREVIEW_MARKDOWN.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_MARKDOWN.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def prepare_or_continue() -> dict[str, Any]:
    project = prepare_full_project()
    initialize_full_review_state()
    progress = refresh_progress_reports()
    terminology = run_nonblocking_terminology_audit()
    return {"project": project, "progress": progress, "terminology": terminology}


def complete_review_batch(
    selected_po: list[str],
    confirm: Callable[[dict[str, Any]], bool],
    *,
    test_runner: Callable[[], dict[str, Any]] = run_full_tests,
    finalize: bool = False,
) -> dict[str, Any]:
    reviewed_at = _now()
    print("正在生成隔离回写预览并检查候选……", flush=True)
    (ROOT / "outputs").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ipg-review-writeback-", dir=ROOT / "outputs") as temporary:
        candidate_root = Path(temporary) / "candidate"
        preview = preview_full_writeback(
            selected_po, candidate_root, recorded_at=reviewed_at
        )
        _write_json(PREVIEW_JSON, preview)
        _write_preview_markdown(preview)
        if not confirm(preview):
            return {"cancelled": True, "preview": preview}

        formal_before = {
            relative: (ROOT / relative).read_bytes()
            for relative in preview["candidateFiles"]
        }
        mapping_before = MAPPING_PATH.read_bytes()
        state_paths = (
            full_review.REVIEW_LEDGER_PATH, full_review.TRANSLATION_NOTES_PATH,
            full_review.REVIEW_ACTIONS_PATH, full_review.PROGRESS_JSON,
            full_review.PROGRESS_MARKDOWN, full_review.TERMINOLOGY_JSON,
            full_review.TERMINOLOGY_MARKDOWN,
        )
        state_before = {path: path.read_bytes() if path.exists() else None for path in state_paths}
        staged_candidate = Path(temporary) / "reading"
        try:
            print("正在重新检查预览基线并最小写回……", flush=True)
            applied = apply_full_writeback(preview, candidate_root)
            print("正在执行完整自动测试（逐项显示进度）……", flush=True)
            tests = test_runner()
            print("正在验证内容及最终文件生成条件……", flush=True)
            validation = validate_current_state()
            print("正在生成非阻塞术语报告……", flush=True)
            terminology = run_nonblocking_terminology_audit()
            print("正在连续构建两次并比较候选文件……", flush=True)
            candidate = build_current_candidate(validation, destination=staged_candidate)
            print("正在归档翻译批注和本批审校记录……", flush=True)
            mapping = inspect_target_project()["mapping"]
            notes = extract_project_translation_notes(mapping, recorded_at=reviewed_at)
            ledger = record_review_completion(
                selected_po,
                {change["unitId"] for change in preview["changes"]},
                notes,
                reviewed_at=reviewed_at,
            )
            progress = refresh_progress_reports()
            print("正在检查最终审校状态……", flush=True)
            validation = validate_current_state()
            candidate.update(validation)
            _write_json(staged_candidate / "validation.json", candidate)
            final = None
            if finalize and validation["release"]["valid"]:
                # This is the last fallible publication step. The native builder
                # restores the old dist on failure; the outer handler restores
                # source and review state. Do not publish a candidate afterward.
                print("正在生成最终阅读文档……", flush=True)
                final = build_project(ROOT, profile="release")
            else:
                full_review._replace_candidate(staged_candidate, CURRENT_CANDIDATE)
        except BaseException:
            rollback_applied_writeback(formal_before, mapping_before)
            for path, content in state_before.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            raise
    return {
        "cancelled": False,
        "preview": preview,
        "applied": applied,
        "tests": tests,
        "validation": validation,
        "terminology": terminology,
        "candidate": candidate,
        "notes": notes,
        "ledger": ledger,
        "progress": progress,
        "final": final,
    }


def parse_file_selection(answer: str, filenames: list[str]) -> list[str]:
    normalized = answer.strip().casefold()
    if normalized in {"q", "quit", "退出", "c", "cancel", "取消"}:
        return []
    if normalized in {"a", "all", "全部"}:
        return list(filenames)
    selected = []
    for token in normalized.replace("，", ",").split(","):
        token = token.strip()
        if not token.isdigit() or not 1 <= int(token) <= len(filenames):
            raise ValueError(f"请输入 1—{len(filenames)} 的编号、a 或 q。")
        name = filenames[int(token) - 1]
        if name not in selected:
            selected.append(name)
    return selected


def _choose_po() -> list[str] | None:
    inspection = inspect_target_project()
    modified = inspection["modifiedPo"]
    print("\n检测到尚未回写修改的文件：")
    print("  " + ("、".join(modified) if modified else "无"))
    print("\n请选择已经逐条审完的文件：")
    for index, name in enumerate(full_review.po_order(), 1):
        marker = "（有修改）" if name in modified else ""
        print(f"  {index}. {name}{marker}")
    print("直接回车采用自动检测结果；输入 a 选择全部；输入逗号分隔的编号；输入 q 取消。")
    while True:
        raw = input("\n请输入编号：").strip()
        if not raw and modified:
            return modified
        try:
            return parse_file_selection(raw, full_review.po_order()) or None
        except ValueError as error:
            print(f"输入错误：{error}")


def _confirm(preview: dict[str, Any]) -> bool:
    print("\n回写预览：")
    print(f"  本批文件：{'、'.join(preview['selectedPo'])}")
    print(f"  已审单元：{preview['reviewedUnitCount']}")
    print(f"  实际修改：{preview['actualChangeCount']}")
    print(f"  同步汇总中文：{len(preview.get('derivedChanges', []))}")
    print(f"  确认保留旧译：{preview['reviewedUnchangedCount']}")
    print(f"  含句段批注的单元：{preview['noteUnitCount']}")
    print(f"  差异报告：{PREVIEW_MARKDOWN}")
    return input("\n确认以上文件已完整审校并继续？[y/N]：").strip().casefold() in {"y", "yes"}


def _show_progress() -> None:
    report = refresh_progress_reports()
    counts = report["counts"]
    print("\n当前审校进度：")
    print(f"  总单元：{report['total']}")
    print(f"  已审未改：{counts['reviewed-unchanged']}")
    print(f"  已审有改：{counts['reviewed-modified']}")
    print(f"  失效：{counts['stale']}")
    print(f"  未审：{counts['unreviewed']}")
    print(f"  详细报告：{PROGRESS_MARKDOWN}；{PROGRESS_JSON}")
    print(f"  术语报告：{TERMINOLOGY_MARKDOWN}；{TERMINOLOGY_JSON}")



def check_formal_release() -> dict[str, Any]:
    report = validate_project(ROOT, profile="release")
    print("\n本地最终文件生成条件：\n" + describe_validation(report))
    return report


def finish_formal_release() -> dict[str, Any] | None:
    if not check_formal_release()["valid"]:
        print("不会创建或更新 dist；仍可使用选项 2 生成候选阅读文档。")
        return None
    print("正在临时构建、独立验证并比较两次结果……", flush=True)
    report = build_project(ROOT, profile="release")
    print(f"已生成最终文件：{ROOT / 'dist/IPG.md'}、{ROOT / 'dist/rules.json'}")
    for name in ("IPG.md", "rules.json"):
        print(f"  SHA-256 {name}：{report['hashes'][name]}")
    print("请检查并提交源、审校记录和最终文件；GitHub 会执行独立的验证与发布。")
    return report


def run_menu(*, candidate_only: bool = False, test_runner=run_full_tests) -> int:
    print("IPG 中文翻译工作流")
    print("  1. 准备或继续 OmegaT 审校")
    print("  2. 完成审校并生成最终文档")
    print("  3. 查看审校进度")
    print("  q. 退出")
    while True:
        choice = input("\n请选择：").strip().casefold()
        if choice == "1":
            result = prepare_or_continue()
            action = "已创建" if result["project"]["created"] else "已验证，可继续使用"
            print(f"\nOmegaT 项目{action}：{PROJECT_DIR / 'omegat.project'}")
            print("完成一批审校后，请在 OmegaT 中选择“项目 → 创建已译文档”。")
            return 0
        if choice == "2":
            selected = _choose_po()
            if selected is None:
                return 2
            result = complete_review_batch(selected, _confirm, test_runner=test_runner, finalize=not candidate_only)
            if result["cancelled"]:
                print("已取消；正式 YAML、审校账本和输出均未改变。")
                return 2
            if result["final"] is not None:
                print(f"已生成：{ROOT / 'dist/IPG.md'}、{ROOT / 'dist/rules.json'}")
            else:
                print(f"本批审校已保存，候选阅读文档：{CURRENT_CANDIDATE}")
                if not candidate_only:
                    print("全文尚未满足最终文件生成条件，旧 dist 保持不变：")
                    print(describe_validation(result["validation"]["release"]))
            print("请用 git diff 检查并提交 YAML、术语库和审校记录；不要手工修改 dist。")
            return 0
        if choice == "3":
            _show_progress()
            return 0
        if choice in {"q", "quit", "退出", "4"}:
            print("已退出；没有修改任何文件。")
            return 0
        # Keep the old final-build shortcuts usable without crowding the menu.
        if choice == "5":
            check_formal_release()
            return 0
        if choice == "6":
            return 0 if finish_formal_release() is not None else 1
        print("输入错误：请输入 1、2、3 或 q。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交互式完成 IPG OmegaT 审校回写流程")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--candidate-only", action="store_true", help="仅保留候选阅读文档，不生成 dist")
    mode.add_argument("--check-release", action="store_true", help="只读检查最终文件生成条件")
    mode.add_argument("--build-release", action="store_true", help="从已审源生成最终文件")
    args = parser.parse_args(argv)
    try:
        from .project_context import project_context
        with project_context(args.project_root):
            if args.check_release:
                return 0 if check_formal_release()["valid"] else 1
            if args.build_release:
                return 0 if finish_formal_release() is not None else 1
            return run_menu(candidate_only=args.candidate_only)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except (OSError, ValueError, KeyError) as error:
        print(f"\n审校助手停止：{error}")
        return 1



if __name__ == "__main__":
    raise SystemExit(main())
