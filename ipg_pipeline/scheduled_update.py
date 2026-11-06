"""Run the isolated update checker and notify once per official PDF in Actions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import shutil
import re

from .core import ROOT
from .update_pr import previous_review
from .official_update import check_official_update
from .snapshot import verify_snapshot


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


def stage_review_snapshot(report: dict) -> Path:
    source = (ROOT / report["snapshot"]).resolve()
    if not source.is_relative_to((ROOT / "outputs/official-update").resolve()):
        raise ValueError("官方候选快照不在隔离输出目录")
    manifest = verify_snapshot(source)
    official = report["official"]
    if any(manifest["official"][key] != official[key] for key in ("effectiveDate", "pdfSha256")):
        raise ValueError("检查结果与候选快照的官方身份不一致")
    destination = ROOT / "snapshots/updates" / official["effectiveDate"] / official["pdfSha256"] / source.name
    if not destination.resolve().is_relative_to((ROOT / "snapshots/updates").resolve()):
        raise ValueError("审阅快照路径越界")
    if destination.exists():
        if verify_snapshot(destination) != manifest:
            raise ValueError("已有审阅快照内容不同，拒绝覆盖")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        verify_snapshot(destination)
    return destination


def notify_update(report: dict, body: Path, repo: str) -> str | None:
    if not report["changed"]:
        return None
    digest = report["official"]["pdfSha256"]
    date = report["official"]["effectiveDate"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("官方更新身份无效")
    branch = f"automation/ipg-{date}-{digest[:12]}"
    existing = previous_review(ROOT, repo, branch, retry_closed=os.environ.get("RETRY_CLOSED") == "true")
    if existing:
        print(f"Review status: {existing['status']}", flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
                stream.write(f"\n更新审阅：{existing['status']} — {existing['url']}\n")
        return existing["url"]
    remote = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
                            cwd=ROOT, stdout=subprocess.PIPE, text=True)
    if remote.returncode != 2:
        if remote.returncode:
            raise subprocess.CalledProcessError(remote.returncode, remote.args)
        raise ValueError(f"自动分支已有但没有 PR，请先人工检查：{branch}")
    snapshot = stage_review_snapshot(report)
    relative = snapshot.relative_to(ROOT).as_posix()
    with body.open("a", encoding="utf-8") as stream:
        stream.write(f"\n持久化审阅报告：[{relative}/reports/review.md](https://github.com/{repo}/blob/{branch}/{relative}/reports/review.md)\n\n"
                     "- [ ] 检查英文差异、身份映射及未决问题\n"
                     "- [ ] 完成中文译文和注解审校\n"
                     "- [ ] 按 IPG 原生版本流程提升候选、更新版本说明和 dist\n"
                     "- [ ] 确认全部检查通过后，再标记为可审阅并人工合并\n\n"
                     "本 PR 只保存不可变候选证据；不会自动切换 current-release 或替代人工审校。\n")
    for args in (
        ["git", "config", "user.name", "github-actions[bot]"],
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        ["git", "switch", "-c", branch],
        ["git", "add", "--", relative],
        ["git", "commit", "-m", f"检测到官方 IPG {date} 更新"],
        ["git", "push", "--set-upstream", "origin", branch],
    ):
        subprocess.run(args, cwd=ROOT, check=True, text=True)
    result = subprocess.run(
        ["gh", "pr", "create", "--repo", repo, "--draft", "--base", "main", "--head", branch,
         "--assignee", repo.split("/")[0],
         "--title", f"IPG {date} 官方更新（待人工校对）",
         "--body-file", str(body)], check=True, stdout=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    report = check_official_update(ROOT, diagnostics_dir=Path("tmp/official-update-diagnostics"))
    result = ROOT / "tmp/official-update-result.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    body = write_report(report, ROOT / "outputs/official-update", run_url)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(body.read_text(encoding="utf-8"))
    print(json.dumps({"status": report["status"], "changed": report["changed"],
                      "pdfSha256": report["official"]["pdfSha256"]}, ensure_ascii=False), flush=True)
    url = notify_update(report, body, repo)
    if url:
        print(f"Review pull request: {url}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
