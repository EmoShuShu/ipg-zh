"""Daily maintenance entry points matching the MTR script names."""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path
from .core import ROOT
from .project_context import project_context

from . import full_review
from .review_assistant import prepare_or_continue
from .transaction import restore_on_failure


def prepare_new_version(confirm=None):
    """Archive the complete old engineering directory, preserving all user files."""
    confirm = input if confirm is None else confirm
    project = full_review.PROJECT_DIR
    if not project.exists():
        return prepare_or_continue()
    if not full_review.MAPPING_PATH.is_file():
        raise ValueError("旧工程缺少版本映射，请先人工确认其身份；不会移动未知工程")
    mapping = json.loads(full_review.MAPPING_PATH.read_text(encoding="utf-8"))
    manifest = full_review.full_inputs()[0]
    if mapping["releaseId"] == manifest["releaseId"]:
        return prepare_or_continue()
    if project.is_symlink() or project.is_junction():
        raise ValueError("旧工程不能是链接")
    archive = full_review.ROOT / "outputs/omegat-archive" / ("ipg-" + uuid.uuid4().hex)
    # Recheck the source identity and bytes after the user confirms.
    protected = {p: p.read_bytes() for name in ("src", "schema")
                 for p in (full_review.ROOT / name).rglob("*") if p.is_file()}
    answer = confirm(f"将旧 OmegaT 工程完整归档到 {archive}，并准备新版本工程？[y/N]：")
    if answer.strip().casefold() not in {"y", "yes"}:
        return {"cancelled": True}
    fresh = {p: p.read_bytes() for name in ("src", "schema")
             for p in (full_review.ROOT / name).rglob("*") if p.is_file()}
    if fresh != protected:
        raise ValueError("确认期间版本或源文件发生变化，未移动旧工程")
    paths = [full_review.REVIEW_LEDGER_PATH, full_review.TRANSLATION_NOTES_PATH,
             full_review.REVIEW_ACTIONS_PATH, full_review.PROGRESS_JSON, full_review.PROGRESS_MARKDOWN,
             full_review.TERMINOLOGY_JSON, full_review.TERMINOLOGY_MARKDOWN]
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        project.rename(archive)
        with restore_on_failure(paths):
            result = prepare_or_continue()
    except BaseException:
        if archive.exists():
            if project.exists():
                shutil.rmtree(project)
            archive.rename(project)
        raise
    return {**result, "archivedProject": str(archive)}


def _run(name, description, operation, argv=None):
    parser = argparse.ArgumentParser(prog=name, description=description)
    if name == "ipg-review-status":
        parser.add_argument("action", choices=["status"], nargs="?", default="status")
    if name == "ipg-export-omegat":
        parser.add_argument("--new-version", action="store_true", help="确认后完整归档旧版本工程，再创建新工程")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    if name == "ipg-audit-terminology":
        parser.add_argument("--fail-on-errors", action="store_true", help="显式启用严格术语检查")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.json and getattr(args, "new_version", False):
        parser.error("--json 仅用于无需交互的准备/继续；接入新版本请使用 --new-version")
    try:
        with project_context(args.project_root):
            result = prepare_new_version() if getattr(args, "new_version", False) else operation()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif name == "ipg-review-status":
                from .maintenance_report import print_progress
                print_progress(result["maintenance"], full_review.PROGRESS_MARKDOWN)
            elif name == "ipg-audit-terminology":
                print(f"术语检查完成（仅供参考）：{result.get('findingCount', 0)} 条提示，{result.get('glossaryIssueCount', 0)} 个词汇表问题。")
                if result.get("error"): print("术语检查未完成：" + result["error"])
                print(f"详细报告：{full_review.TERMINOLOGY_MARKDOWN}")
            elif not result.get("cancelled"):
                print(f"OmegaT 工程已准备或继续：{full_review.PROJECT_DIR}（{result['project']['unitCount']} 个单元）")
                if result.get("archivedProject"): print("旧工程已保留：" + result["archivedProject"])
            if getattr(args, "fail_on_errors", False) and (result.get("error") or result.get("glossaryIssueCount", 0)):
                return 1
            if result.get("cancelled"):
                return 2
        return 0
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except (OSError, ValueError, KeyError) as error:
        print(f"操作已停止：{error}")
        return 1


def export_main(argv=None):
    return _run("ipg-export-omegat", "准备或继续现有全文 OmegaT 工程，不覆盖用户编辑",
                prepare_or_continue, argv)


def status_main(argv=None):
    return _run("ipg-review-status", "刷新审校进度报告；登记已审文件请使用审校助手",
                full_review.refresh_progress_reports, argv)


def terminology_main(argv=None):
    return _run("ipg-audit-terminology", "生成非阻塞术语审计报告",
                full_review.run_nonblocking_terminology_audit, argv)
