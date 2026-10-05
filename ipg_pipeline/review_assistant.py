from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .core import ROOT
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


PREVIEW_JSON = ROOT / "outputs/omegat-writeback-preview.json"
PREVIEW_MARKDOWN = ROOT / "outputs/omegat-writeback-preview.md"


def run_full_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        raise ValueError("完整测试失败：\n" + result.stdout)
    return {"passed": True, "output": result.stdout.strip()}


def _write_preview_markdown(preview: dict[str, Any]) -> None:
    lines = [
        "# OmegaT 回写预览",
        "",
        f"- 本批文件：{', '.join(preview['selectedPo'])}",
        f"- 已审单元：{preview['reviewedUnitCount']}",
        f"- 实际修改：{preview['actualChangeCount']}",
        f"- 确认保留旧译：{preview['reviewedUnchangedCount']}",
        f"- 含句段批注的单元：{preview['noteUnitCount']}",
        f"- 12 项初始缺译中尚余：{preview['initialMissingRemaining']}",
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
) -> dict[str, Any]:
    reviewed_at = _now()
    with tempfile.TemporaryDirectory(prefix="ipg-review-writeback-") as temporary:
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
        try:
            applied = apply_full_writeback(preview, candidate_root)
            tests = test_runner()
            validation = validate_current_state()
            terminology = run_nonblocking_terminology_audit()
            candidate = build_current_candidate(validation)
            mapping = inspect_target_project()["mapping"]
            notes = extract_project_translation_notes(mapping, recorded_at=reviewed_at)
            ledger = record_review_completion(
                selected_po,
                {change["unitId"] for change in preview["changes"]},
                notes,
                reviewed_at=reviewed_at,
            )
            progress = refresh_progress_reports()
        except Exception:
            rollback_applied_writeback(formal_before, mapping_before)
            if CURRENT_CANDIDATE.exists():
                shutil.rmtree(CURRENT_CANDIDATE)
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
    }


def _choose_po() -> list[str] | None:
    inspection = inspect_target_project()
    modified = inspection["modifiedPo"]
    print("\n检测到尚未回写修改的文件：")
    print("  " + ("、".join(modified) if modified else "无"))
    print("\n请选择已经逐条审完的文件：")
    for index, name in enumerate(PO_ORDER, 1):
        marker = "（有修改）" if name in modified else ""
        print(f"  {index}. {name}{marker}")
    print("直接回车采用自动检测结果；输入 all 选择全部；输入逗号分隔的编号；输入 c 取消。")
    raw = input("> ").strip().casefold()
    if raw in {"c", "cancel", "取消"}:
        return None
    if not raw:
        if not modified:
            print("没有自动检测到修改；请明确选择已逐条审完的文件。")
            return None
        return modified
    if raw in {"all", "全部"}:
        return PO_ORDER.copy()
    try:
        indexes = {int(item.strip()) for item in raw.replace("，", ",").split(",")}
    except ValueError as error:
        raise ValueError("请输入 1—8 的编号、all 或 c。") from error
    if not indexes or min(indexes) < 1 or max(indexes) > len(PO_ORDER):
        raise ValueError("文件编号超出 1—8。")
    return [name for index, name in enumerate(PO_ORDER, 1) if index in indexes]


def _confirm(preview: dict[str, Any]) -> bool:
    print("\n回写预览：")
    print(f"  本批文件：{'、'.join(preview['selectedPo'])}")
    print(f"  已审单元：{preview['reviewedUnitCount']}")
    print(f"  实际修改：{preview['actualChangeCount']}")
    print(f"  确认保留旧译：{preview['reviewedUnchangedCount']}")
    print(f"  含句段批注的单元：{preview['noteUnitCount']}")
    print(f"  12 项初始缺译中尚余：{preview['initialMissingRemaining']}")
    print(f"  差异报告：{PREVIEW_MARKDOWN}")
    return input("确认写回并执行全部检查？请输入 yes：").strip().casefold() == "yes"


def _show_progress() -> None:
    report = refresh_progress_reports()
    counts = report["counts"]
    print("\n当前审校进度：")
    print(f"  总单元：{report['total']}")
    print(f"  已审未改：{counts['reviewed-unchanged']}")
    print(f"  已审有改：{counts['reviewed-modified']}")
    print(f"  stale：{counts['stale']}")
    print(f"  未审：{counts['unreviewed']}")
    print(f"  12 项初始缺译中尚余：{report['initialMissingRemaining']}")
    print(f"  详细报告：{PROGRESS_MARKDOWN}；{PROGRESS_JSON}")
    print(f"  术语报告：{TERMINOLOGY_MARKDOWN}；{TERMINOLOGY_JSON}")


def _pause() -> None:
    input("\n按回车键返回主菜单……")


def main() -> None:
    while True:
        print("\nIPG 全文审校助手（当前内容不可正式发布）")
        print("1. 准备或继续 OmegaT 审校")
        print("2. 完成审校并生成候选阅读文档")
        print("3. 查看审校进度")
        print("4. 退出")
        choice = input("> ").strip()
        try:
            if choice == "1":
                result = prepare_or_continue()
                action = "已创建" if result["project"]["created"] else "已验证，可继续使用"
                print(f"\nOmegaT 项目{action}：{PROJECT_DIR / 'omegat.project'}")
                print("完成一批审校后，请在 OmegaT 中选择“项目 → 创建已译文档”。")
                _pause()
            elif choice == "2":
                selected = _choose_po()
                if selected is not None:
                    result = complete_review_batch(selected, _confirm)
                    if result["cancelled"]:
                        print("已取消；正式 YAML、审校账本和候选输出均未改变。")
                    else:
                        print(f"候选阅读文档已生成：{CURRENT_CANDIDATE}")
                        print("这是不可发布的 candidate，不是正式发布文件。")
                _pause()
            elif choice == "3":
                _show_progress()
                _pause()
            elif choice == "4":
                return
            else:
                print("请输入 1、2、3 或 4。")
        except Exception as error:
            print(f"\n操作已停止：{error}")
            print("必要检查未通过；不会登记为审校完成。")
            _pause()


if __name__ == "__main__":
    main()
