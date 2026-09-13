"""Stamp asalloc version and GitHub URL into the copied web visualizer."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLACEHOLDER_VERSION = "__ASALLOC_VERSION__"
_PLACEHOLDER_REPO = "__ASALLOC_REPO_URL__"


def project_version(root: Path | None = None) -> str:
    text = (root or _REPO_ROOT).joinpath("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "(.+)"', text, re.M)
    if match is None:
        raise RuntimeError("could not read project version from pyproject.toml")
    return match.group(1)


def github_repo_url() -> str:
    remote = subprocess.check_output(
        ["git", "remote", "get-url", "origin"],
        text=True,
        cwd=_REPO_ROOT,
    ).strip()
    url = re.sub(r"^git@github\.com:([^/]+)/(.+)$", r"https://github.com/\1/\2", remote)
    url = re.sub(r"^ssh://git@github\.com/([^/]+)/(.+)$", r"https://github.com/\1/\2", url)
    return re.sub(r"\.git$", "", url)


def stamp_index(index_path: Path | None = None) -> None:
    path = index_path or (Path.home() / ".local" / "asalloc" / "visualizer" / "index.html")
    version = project_version()
    url = github_repo_url()
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace(_PLACEHOLDER_VERSION, version)
        .replace(_PLACEHOLDER_REPO, url),
        encoding="utf-8",
    )


def main() -> None:
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    stamp_index(path)


if __name__ == "__main__":
    main()
