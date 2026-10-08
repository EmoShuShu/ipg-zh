"""MTR-style GitHub publication; existing IPG builders remain the only builders."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from .builder import build_outputs, read_version_notes
from .core import ROOT, sha256_bytes
from .production import (_unchanged, _validate_project, candidate_destination,
                         describe_validation, load_project, validate_output)


ASSETS = ("IPG.md", "rules.json", "SHA256SUMS")


def prepare_release(root: Path, artifacts: Path, commit: str) -> tuple[dict, dict]:
    root = root.resolve()
    artifacts = candidate_destination(root, artifacts)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("来源提交必须是完整 Git commit SHA。")
    project = load_project(root)
    validation = _validate_project(project, profile="release")
    if not validation["valid"]:
        raise ValueError("发布准备已拒绝：\n" + describe_validation(validation))
    for name in (*ASSETS, "RELEASE_NOTES.md", "release-metadata.json"):
        path = artifacts / name
        if path.is_symlink() or path.is_junction():
            raise ValueError("发布资料不能是链接：" + name)
    if not validate_output(artifacts / "rules.json", root)["valid"]:
        raise ValueError("发布 JSON schema 不合法。")
    # Reuse the real builder to prove that artifacts still match current source.
    with tempfile.TemporaryDirectory(prefix="ipg-release-check-") as name:
        expected = Path(name)
        build_outputs(expected, project["manifest"], [doc for _, doc in project["documents"]],
                      project["display"], candidate=False, profile="release", source_root=root)
        for filename in ASSETS:
            if (artifacts / filename).read_bytes() != (expected / filename).read_bytes():
                raise ValueError("发布资料与当前源不一致：" + filename)
        for filename in ASSETS[:2]:
            if (root / "dist" / filename).read_bytes() != (expected / filename).read_bytes():
                raise ValueError("dist 已过期：" + filename)
    _unchanged(project["hashes"])
    hashes = {name: sha256_bytes((artifacts / name).read_bytes()) for name in ASSETS}
    manifest = project["manifest"]
    versions = manifest["versions"]
    metadata = {"releaseId": manifest["releaseId"], "sourceCommit": commit,
                "tag": f"{manifest['releaseId']}-{hashes['rules.json'][:12]}",
                "title": f"IPG 中文版 {versions['official']['effectiveDate']} · {versions['translation']['revision']}",
                "versions": versions, "hashes": hashes}
    notes = (f"# {metadata['title']}\n\n"
             "本 Release 在 main 的最终产物或版本资料更新后，经完整检查自动生成。\n\n"
             f"- 官方 IPG：{versions['official']['effectiveDate']}\n"
             f"- AIPG 注解：{versions['annotations']['version']}（{versions['annotations']['sourceId']}）\n"
             f"- 中文修订：{versions['translation']['revision']}\n"
             f"- 完整版本身份：`{manifest['releaseId']}`\n"
             f"- 来源提交：`{commit}`\n"
             f"- rules.json SHA-256：`{hashes['rules.json']}`\n\n"
             "附件为 IPG.md、rules.json 和 SHA256SUMS；许可/署名及 publishable 来源记录未被程序改写。\n\n"
             "## 当前版本说明\n\n" + read_version_notes(root) + "\n")
    for name, data in [("RELEASE_NOTES.md", notes.encode("utf-8")),
                       ("release-metadata.json", (json.dumps(metadata, ensure_ascii=False, indent=2,
                                                             sort_keys=True) + "\n").encode("utf-8"))]:
        path = artifacts / name
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("发布证据已存在且内容不同，不会覆盖：" + name)
        else:
            with path.open("xb") as stream:
                stream.write(data)
    hashes_to_check = {**project["hashes"],
                       **{str(artifacts / name): sha256_bytes((artifacts / name).read_bytes())
                          for name in (*ASSETS, "RELEASE_NOTES.md", "release-metadata.json")}}
    _unchanged(hashes_to_check)
    return metadata, hashes_to_check


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)


def _checked(root: Path, *args: str) -> str:
    result = _run(root, *args)
    if result.returncode:
        raise ValueError(f"{args[0]} 操作失败：{result.stderr.strip()}")
    return result.stdout.strip()


def _release(root: Path, repo: str, tag: str) -> dict | None:
    result = _run(root, "gh", "api", f"repos/{repo}/releases/tags/{quote(tag, safe='')}", "--include")
    text = result.stdout.replace("\r\n", "\n")
    header = re.match(r"HTTP/\S+ (\d{3})[^\n]*\n", text)
    status = int(header[1]) if header else None
    if status == 404:
        return None
    if result.returncode or status != 200:
        raise ValueError("GitHub 查询失败，不会视为版本不存在：" + result.stderr.strip())
    release = json.loads(text.split("\n\n", 1)[1])
    if (not isinstance(release, dict) or release.get("tag_name") != tag
            or type(release.get("draft")) is not bool or not isinstance(release.get("assets"), list)):
        raise ValueError("GitHub Release 响应结构不合法。")
    return release


def _tag_commit(root: Path, tag: str) -> str | None:
    ref = "refs/tags/" + tag
    text = _checked(root, "git", "ls-remote", "origin", ref, ref + "^{}")
    refs = {}
    for line in text.splitlines():
        sha, name = line.split()
        if name not in (ref, ref + "^{}") or name in refs or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("远端标签响应不合法。")
        refs[name] = sha
    return refs.get(ref + "^{}", refs.get(ref))


def _verify_assets(root: Path, repo: str, tag: str, artifacts: Path, release: dict) -> set[str]:
    names = [asset.get("name") for asset in release["assets"]]
    if len(names) != len(set(names)) or not set(names).issubset(ASSETS):
        raise ValueError("已有 Release 含重复或非预期附件，不会覆盖。")
    if names:
        with tempfile.TemporaryDirectory(prefix="ipg-existing-release-") as directory:
            patterns = [item for name in names for item in ("--pattern", name)]
            _checked(root, "gh", "release", "download", tag, "--repo", repo, "--dir", directory, *patterns)
            for name in names:
                if (Path(directory) / name).read_bytes() != (artifacts / name).read_bytes():
                    raise ValueError("已有 Release 附件不同，不会覆盖：" + name)
    return set(names)


def publish_release(root: Path, artifacts: Path, commit: str, repo: str) -> dict:
    root = root.resolve()
    artifacts = candidate_destination(root, artifacts)
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub 仓库标识不合法。")
    origin = _checked(root, "git", "remote", "get-url", "origin").removesuffix(".git").lower()
    if origin not in (f"https://github.com/{repo}".lower(), f"git@github.com:{repo}".lower()):
        raise ValueError("origin 与指定发布仓库不同。")
    if _checked(root, "git", "rev-parse", "HEAD") != commit:
        raise ValueError("来源提交与当前检出不同。")
    _checked(root, "git", "diff", "--quiet", "HEAD", "--")
    metadata, hashes = prepare_release(root, artifacts, commit)
    tag = metadata["tag"]
    release = _release(root, repo, tag)
    target = _tag_commit(root, tag)
    if release is not None:
        if (not target or f"- 来源提交：`{target}`" not in release.get("body", "")
                or _run(root, "git", "merge-base", "--is-ancestor", target, commit).returncode):
            raise ValueError("已有 Release 的标签、来源提交或 main 历史不一致。")
        existing = _verify_assets(root, repo, tag, artifacts, release)
        if not release["draft"]:
            if existing != set(ASSETS):
                raise ValueError("已公开 Release 缺少附件，不会自动改写。")
            _unchanged(hashes)
            return {**metadata, "status": "verified-existing", "url": release.get("html_url")}
    else:
        existing = set()
    # Only current main can create/publish. Old published releases can be verified read-only.
    if _checked(root, "git", "ls-remote", "origin", "refs/heads/main").split() != [commit, "refs/heads/main"]:
        raise ValueError("main 已变化或当前检出不是 main；不会发布旧运行。")
    if target is not None and target != commit:
        raise ValueError("标签指向不同提交；不会移动标签或接管旧草稿。")
    _unchanged(hashes)
    if release is None:
        if target is None:
            _checked(root, "gh", "api", f"repos/{repo}/git/refs", "--method", "POST",
                     "--raw-field", f"ref=refs/tags/{tag}", "--raw-field", f"sha={commit}")
        if _tag_commit(root, tag) != commit:
            raise ValueError("无法确认新标签的来源提交，未创建 Release。")
        _checked(root, "gh", "release", "create", tag, *(str(artifacts / name) for name in ASSETS),
                 "--repo", repo, "--draft", "--verify-tag", "--target", commit, "--title", metadata["title"],
                 "--notes-file", str(artifacts / "RELEASE_NOTES.md"))
    elif missing := sorted(set(ASSETS) - existing):
        _checked(root, "gh", "release", "upload", tag, *(str(artifacts / name) for name in missing), "--repo", repo)
    release = _release(root, repo, tag)
    if (release is None or not release["draft"] or f"- 来源提交：`{commit}`" not in release.get("body", "")
            or _tag_commit(root, tag) != commit
            or _verify_assets(root, repo, tag, artifacts, release) != set(ASSETS)):
        raise ValueError("草稿或附件核验失败，未公开发布。")
    _unchanged(hashes)
    if _checked(root, "git", "ls-remote", "origin", "refs/heads/main").split() != [commit, "refs/heads/main"]:
        raise ValueError("核验期间 main 已变化；保留草稿，不会公开。")
    _checked(root, "gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest=true")
    published = _release(root, repo, tag)
    if published is None or published["draft"]:
        raise ValueError("无法确认公开发布结果；请查看运行记录，不会重新覆盖。")
    return {**metadata, "status": "published", "url": published.get("html_url")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="准备或发布已经验证的 IPG 资料；默认只准备。")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    try:
        if args.publish:
            report = publish_release(root, root / args.artifacts, args.source_commit, args.repo or "")
        else:
            report, _ = prepare_release(root, root / args.artifacts, args.source_commit)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"发布流程已停止：{error}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            message = str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::error::" + message)
        return 1
