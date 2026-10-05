from __future__ import annotations

import re
from pathlib import Path

from .core import ROOT


def current_release_dir(root: Path = ROOT) -> Path:
    """Resolve a contained release identity; never guess a latest version."""
    identity = (root / "src/ipg/current-release.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"ipg-[A-Za-z0-9_-]+", identity):
        raise ValueError("当前 release 指针无效：应为完整 release ID，不能填写路径。")
    base = (root / "src/ipg/releases").resolve()
    release = (base / identity).resolve()
    if not release.is_relative_to(base) or not (release / "manifest.yaml").is_file():
        raise ValueError(f"当前 release 的 manifest 不存在或越界：{identity}")
    return release
