"""End-to-end runs of the git-based checks against a throwaway repository. No network."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SCRIPTS

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


def test_diff_check_flags_dco_and_scoped_ascii(setup):
    repo, home = setup
    result = run("diff_check.py", repo, home, "acme/widgets", "--base", "main", "--plan", "pkg/a.py,docs/*")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Signed-off-by" in result.stdout
    assert "docs/note.md:1: non-ASCII" in result.stdout
    assert "pkg/a.py" not in result.stdout.split("RESULT")[0].split("non-ASCII")[0].split("FAIL")[-1]


def test_diff_check_passes_when_clean(setup):
    repo, home = setup
    (repo / "docs" / "note.md").write_text("Empty lists now raise.\n", encoding="utf-8")
    git(repo, "reset", "-q", "--soft", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fix: raise on empty input\n\nSigned-off-by: T <t@example.com>")
    result = run("diff_check.py", repo, home, "acme/widgets", "--base", "main", "--plan", "pkg/a.py,docs/*")
    assert result.returncode == 0, result.stdout + result.stderr


def test_pr_body_check(setup):
    repo, home = setup
    body = repo / "body.md"
    body.write_text(
        "## Summary\n\nRaise `ValueError` from `run()` on empty input. Refs #12\n\n"
        "## Checklist\n\n- [x] I added tests\n\nFrom Claude Code\n",
        encoding="utf-8",
    )
    ok = run("pr_body_check.py", repo, home, "acme/widgets", "--body", str(body), "--issue", "12", "--base", "main")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    body.write_text("Fixes #12, it returned 500 before.\n", encoding="utf-8")
    bad = run(
        "pr_body_check.py",
        repo,
        home,
        "acme/widgets",
        "--body",
        str(body),
        "--issue",
        "12",
        "--umbrella",
        "--base",
        "main",
    )
    assert bad.returncode == 1
    for expected in (
        "heading missing",
        "checklist item missing",
        "FAIL: required AI disclosure not found",
        "closing keyword",
        "number 500",
    ):
        assert expected in bad.stdout, expected


def test_findings_check(setup):
    repo, home = setup
    report = {
        "coverage": [
            {"path": "pkg/a.py", "status": "reviewed"},
            {"path": "docs/note.md", "status": "skipped", "reason": "prose"},
        ],
        "tests": [
            {
                "name": "test_empty",
                "fails_without_fix": True,
                "without_fix_log": "without.txt",
                "with_fix_log": "with.txt",
            }
        ],
        "findings": [
            {
                "path": "pkg/a.py",
                "start_line": 2,
                "end_line": 3,
                "severity": "low",
                "category": "style",
                "evidence": "message could name the argument",
                "resolution": "dismissed: matches the error style used elsewhere in pkg/",
            }
        ],
    }
    (repo / "without.txt").write_text("test_empty FAILED\nValueError not raised\n1 failed\n", encoding="utf-8")
    (repo / "with.txt").write_text("test_empty PASSED\n1 passed\n", encoding="utf-8")
    path = repo / "review.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    ok = run("findings_check.py", repo, home, str(path), "--base", "main")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    report["coverage"] = report["coverage"][:1]
    report["findings"][0]["start_line"] = 99
    path.write_text(json.dumps(report), encoding="utf-8")
    bad = run("findings_check.py", repo, home, str(path), "--base", "main")
    assert bad.returncode == 1
    assert "docs/note.md: not in coverage" in bad.stdout
    assert "do not exist in the new file" in bad.stdout


def test_default_branch_is_detected_not_assumed(tmp_path):
    """mlflow's default branch is master; scripts must not assume upstream/main."""
    upstream, clone, home = tmp_path / "up", tmp_path / "clone", tmp_path / "home"
    upstream.mkdir()
    home.mkdir()
    git(upstream, "init", "-q", "-b", "master")
    (upstream / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(upstream, "add", ".")
    git(upstream, "commit", "-q", "-m", "init")
    git(tmp_path, "clone", "-q", "-o", "upstream", str(upstream), str(clone))
    git(clone, "checkout", "-q", "-b", "fix/x")
    (clone / "a.py").write_text("x = 2\n", encoding="utf-8")
    git(clone, "commit", "-q", "-a", "-m", "fix: x")
    result = run("diff_check.py", clone, home, "acme/none")
    assert "against upstream/master" in result.stdout, result.stdout + result.stderr
    rules = run("rules_for.py", clone, home, "acme/none", "--changed")
    assert "a.py" in rules.stdout, rules.stdout + rules.stderr
