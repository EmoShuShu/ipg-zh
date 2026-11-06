"""Daily maintenance entry points matching the MTR script names."""
from __future__ import annotations

import argparse
import json

from . import full_review
from .review_assistant import prepare_or_continue


def _run(name, description, operation, argv=None):
    parser = argparse.ArgumentParser(prog=name, description=description)
    if name == "ipg-review-status":
        parser.add_argument("action", choices=["status"], nargs="?", default="status")
    parser.parse_args(argv)
    try:
        print(json.dumps(operation(), ensure_ascii=False, indent=2))
        return 0
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
