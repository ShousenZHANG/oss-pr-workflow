"""Shared test setup: the scripts directory on sys.path, and a throwaway git repository with a profile."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "oss-pr" / "scripts"
sys.path.insert(0, str(SCRIPTS))

PROFILE = """# acme/widgets

## Facts

- checked: 2026-10-03
- ai_policy: disclosure
- disclosure_regex: (?m)^From \\S+
- dco: yes
- ascii_only: docs/**
- issue_required: yes
- merged_pr_files_p90: 5
- merged_pr_lines_p90: 200
"""

TEMPLATE = "## Summary\n\n## Checklist\n\n- [ ] I added tests\n"


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env.update({"GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"})
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout


def run(script: str, repo: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OSS_PR_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


@pytest.fixture
def setup(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    (home / "repos").mkdir(parents=True)
    (home / "repos" / "acme__widgets.md").write_text(PROFILE, encoding="utf-8")
    (home / "cache" / "templates").mkdir(parents=True)
    (home / "cache" / "templates" / "acme__widgets.md").write_text(TEMPLATE, encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("def run(items):\n    return items[0]\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "checkout", "-q", "-b", "fix/empty")
    (repo / "pkg" / "a.py").write_text(
        "def run(items):\n    if not items:\n        raise ValueError('empty')\n    return items[0]\n", encoding="utf-8"
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("Empty lists now raise — clearly.\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fix: raise on empty input")
    return repo, home
