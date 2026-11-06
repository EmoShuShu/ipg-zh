"""Run the isolated update checker and notify once per official PDF in Actions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from .core import ROOT
from .official_update import check_official_update


def write_report(report: dict, output: Path, run_url: str) -> Path:
    official = report["official"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    body = (
        f"<!-- ipg-official-sha256:{official['pdfSha256']} -->\n"
        f"## 官方 IPG 检查：{report['status']}\n\n"
        f"- PDF 有效日期：{official['effectiveDate']}\n"
        f"- 官方文件：{official['url']}\n"
        f"- SHA-256：`{official['pdfSha256']}`\n"
        f"- 当前版本：`{report['baseReleaseId']}`\n"
        f"- 快照：`{report['snapshot']}`\n"
        f"- 检查运行与证据附件：{run_url}\n\n"
        "证据附件保留 30 天，请及时下载。过期后可手动重新运行检查取得新附件。\n\n"
        "检测到变化时，请下载附件中的审阅报告与候选快照，完成英文差异、中文译文及注解复核。"
        "当前版本、审校记录和正式发布文件均未自动改写。\n"
    )
    path = output / "review.md"
    path.write_text(body, encoding="utf-8")
    return path


def notify_update(report: dict, body: Path, repo: str) -> str | None:
    if not report["changed"]:
        return None
    digest = report["official"]["pdfSha256"]
    marker = f"<!-- ipg-official-sha256:{digest} -->"
    # Include closed issues so acknowledging an update does not cause daily reminders.
    result = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "all", "--limit", "1000",
         "--json", "body,url"], check=True, stdout=subprocess.PIPE, text=True,
    )
    for issue in json.loads(result.stdout):
        if marker in (issue.get("body") or ""):
            return issue["url"]
    result = subprocess.run(
        ["gh", "issue", "create", "--repo", repo, "--assignee", repo.split("/")[0],
         "--title", f"IPG {report['official']['effectiveDate']} 官方更新（待人工复核）",
         "--body-file", str(body)], check=True, stdout=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    report = check_official_update(ROOT)
    body = write_report(report, ROOT / "outputs/official-update", run_url)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(body.read_text(encoding="utf-8"))
    print(json.dumps({"status": report["status"], "changed": report["changed"],
                      "pdfSha256": report["official"]["pdfSha256"]}, ensure_ascii=False), flush=True)
    url = notify_update(report, body, repo)
    if url:
        print(f"Review issue: {url}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
