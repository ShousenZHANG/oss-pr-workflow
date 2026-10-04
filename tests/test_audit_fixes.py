"""Gaps found by the 2026-10-04 audit of v1.0.0. Each test reproduces a case a gate let through."""

import json
from datetime import date, datetime, timedelta, timezone

import pacing
import pytest
from _config import load_profile, parse_facts, profile_status
from conftest import git, run
from diff_check import added_lines
from findings_check import changed_ranges, check_tests
from pacing import Limits, MyPR, decide


class TestDiffParsing:
    def test_added_line_starting_with_plus_plus_is_scanned(self):
        """`++counter;` added becomes `+++counter;` in the diff and was taken for a file header."""
        diff = "diff --git a/a.cpp b/a.cpp\n--- a/a.cpp\n+++ b/a.cpp\n@@ -1,0 +2,2 @@\n+++counter;\n++++ x\n"
        assert added_lines(diff) == {"a.cpp": [(2, "++counter;"), (3, "+++ x")]}

    def test_changed_ranges_survive_plus_plus_lines(self):
        """An added `++ b.cpp` line reads `+++ b.cpp` and switched the parser to a file that does not exist."""
        diff = "--- a/a.cpp\n+++ b/a.cpp\n@@ -1,0 +2,1 @@\n+++ b.cpp\n--- a/c.py\n+++ b/c.py\n@@ -5 +5 @@\n-x\n+y\n"
        assert changed_ranges(diff) == {"a.cpp": [(2, 2)], "c.py": [(5, 5)]}

    def test_deleted_file_ranges_are_old_side(self):
        diff = "--- a/old.py\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-a\n-b\n-c\n"
        assert changed_ranges(diff) == {"old.py": [(1, 3)]}


def test_non_ascii_path_is_checked_under_its_real_name(setup):
    """git octal-escapes `docs/中文.md` by default, so the `docs/**` ASCII rule never matched it."""
    repo, home = setup
    (repo / "docs" / "中文.md").write_text("说明\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "docs: add a note\n\nSigned-off-by: T <t@example.com>")
    result = run("diff_check.py", repo, home, "acme/widgets", "--base", "main", "--plan", "pkg/a.py,docs/*")
    assert "docs/中文.md:1: non-ASCII" in result.stdout, result.stdout + result.stderr
    assert "not in the plan" not in result.stdout


class TestReviewGatesSeeTheRealChange:
    def test_uncommitted_fix_fails_both_gates(self, setup):
        """Both gates read base...HEAD; a fix left uncommitted gave a clean 0-file PASS."""
        repo, home = setup
        (repo / "pkg" / "a.py").write_text("def run(items):\n    return items[-1]\n", encoding="utf-8")
        diff = run("diff_check.py", repo, home, "acme/widgets", "--base", "main")
        assert diff.returncode == 1 and "uncommitted changes" in diff.stdout, diff.stdout + diff.stderr
        (repo / "review.json").write_text('{"coverage": [], "tests": [], "findings": []}', encoding="utf-8")
        review = run("findings_check.py", repo, home, "review.json", "--base", "main")
        assert review.returncode == 1 and "uncommitted changes" in review.stdout, review.stdout + review.stderr

    def test_untracked_file_is_a_warning(self, setup):
        repo, home = setup
        (repo / "pkg" / "helper.py").write_text("x = 1\n", encoding="utf-8")
        result = run("diff_check.py", repo, home, "acme/widgets", "--base", "main")
        assert "WARN: 1 untracked file" in result.stdout and "pkg/helper.py" in result.stdout, result.stdout

    def test_empty_change_is_not_a_pass(self, setup):
        repo, home = setup
        git(repo, "checkout", "-q", "main")
        git(repo, "checkout", "-q", "-b", "nothing")
        result = run("diff_check.py", repo, home, "acme/widgets", "--base", "main")
        assert result.returncode == 1 and "no committed change" in result.stdout, result.stdout

    def test_deleted_file_needs_review(self, setup):
        """`--diff-filter=d` left deleted files out of coverage: deleting behavior reviewed nothing."""
        repo, home = setup
        git(repo, "rm", "-q", "pkg/a.py")
        git(repo, "commit", "-q", "-m", "refactor: drop run()")
        report = {
            "coverage": [{"path": "docs/note.md", "status": "skipped", "reason": "prose"}],
            "tests": [
                {"name": "test_x", "fails_without_fix": True, "without_fix_log": "w.txt", "with_fix_log": "f.txt"}
            ],
            "findings": [],
        }
        (repo / "w.txt").write_text("FAILED t.py::test_x\n1 failed\n", encoding="utf-8")
        (repo / "f.txt").write_text("t.py::test_x PASSED\n1 passed\n", encoding="utf-8")
        (repo / "review.json").write_text(json.dumps(report), encoding="utf-8")
        missing = run("findings_check.py", repo, home, "review.json", "--base", "main")
        assert missing.returncode == 1 and "pkg/a.py: not in coverage" in missing.stdout, missing.stdout
        report["coverage"].append({"path": "pkg/a.py", "status": "reviewed"})
        report["findings"] = [
            {
                "path": "pkg/a.py",
                "start_line": 1,
                "end_line": 2,
                "severity": "low",
                "category": "maintainability",
                "evidence": "callers of run() are gone too",
                "resolution": "fixed",
            }
        ]
        (repo / "review.json").write_text(json.dumps(report), encoding="utf-8")
        ok = run("findings_check.py", repo, home, "review.json", "--base", "main")
        assert ok.returncode == 0, ok.stdout + ok.stderr
        assert "coverage 1/2 files reviewed" in ok.stdout


class TestEvidenceIsTheSameTest:
    def check(self, without: str, with_: str) -> list[str]:
        logs = {"without.txt": without, "with.txt": with_}
        test = {"name": "tests/t.py::test_x", "fails_without_fix": True}
        test |= {"without_fix_log": "without.txt", "with_fix_log": "with.txt"}
        return [f.message for f in check_tests([test], read_text=logs.get)]

    def test_other_test_passing_is_not_proof(self):
        messages = self.check("tests/t.py::test_x FAILED\n1 failed", "tests/t.py::test_y PASSED\n1 passed")
        assert any("does not show test_x passing" in m for m in messages), messages

    def test_skipped_run_is_not_a_pass(self):
        messages = self.check("tests/t.py::test_x FAILED\n1 failed", "tests/t.py::test_x SKIPPED\n0 passed, 1 skipped")
        assert any("does not show test_x passing" in m for m in messages), messages

    def test_another_test_failing_is_not_this_test_failing(self):
        without = "tests/t.py::test_x PASSED\ntests/t.py::test_z FAILED\n1 failed, 1 passed"
        messages = self.check(without, "tests/t.py::test_x PASSED\n1 passed")
        assert any("does not show test_x failing" in m for m in messages), messages

    def test_similar_name_does_not_count(self):
        messages = self.check("tests/t.py::test_x_slow FAILED\n1 failed", "tests/t.py::test_x PASSED\n1 passed")
        assert any("does not show test_x failing" in m for m in messages), messages

    def test_real_red_green_passes_across_runners(self):
        assert self.check("FAILED tests/t.py::test_x - AssertionError\n1 failed", "tests/t.py::test_x PASSED") == []
        go = {"name": "TestX", "fails_without_fix": True, "without_fix_log": "a", "with_fix_log": "b"}
        logs = {"a": "--- FAIL: TestX (0.00s)\nFAIL", "b": "--- PASS: TestX (0.00s)\nok"}
        assert check_tests([go], read_text=logs.get) == []
        jest = {"name": "renders empty list", "fails_without_fix": True, "without_fix_log": "a", "with_fix_log": "b"}
        logs = {
            "a": "  ✕ renders empty list (4 ms)\nTests: 1 failed",
            "b": "  ✓ renders empty list (3 ms)\nTests: 1 passed",
        }
        assert check_tests([jest], read_text=logs.get) == []


class TestPacingFailsClosed:
    NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
    LIMITS = Limits(repo_limit=None, per_repo_cap=3, min_interval=timedelta(hours=24), silent_after=timedelta(days=7))

    def test_unreadable_response_on_an_old_pr_stops(self):
        """`gh pr view` failed for a 10-day-old PR; pacing treated it as answered and said OK."""
        old = MyPR(number=7, state="OPEN", created_at=self.NOW - timedelta(days=10), labels=(), responded=None)
        results = decide([old], self.LIMITS, self.NOW)
        assert results[0][0] == "STOP" and "#7" in results[0][1], results

    def test_full_search_page_is_not_complete(self, monkeypatch):
        rows = [{"number": i, "state": "MERGED", "createdAt": "2026-01-01T00:00:00Z", "labels": []} for i in range(100)]
        monkeypatch.setattr(pacing, "gh_json", lambda args: rows)
        with pytest.raises(RuntimeError, match="truncated"):
            pacing.my_prs("a/b", "me", timedelta(days=7), self.NOW)


class TestProfileStatus:
    TODAY = date(2026, 10, 4)

    def test_checked_recently(self, tmp_path):
        assert profile_status({"checked": "2026-09-20"}, tmp_path / "a__b.md", self.TODAY) is None

    @pytest.mark.parametrize(
        ("checked", "expected"),
        [("draft", "not checked"), ("", "not checked"), ("2026-08-01", "64 days ago")],
    )
    def test_unchecked_or_stale(self, tmp_path, checked, expected):
        assert expected in profile_status({"checked": checked}, tmp_path / "a__b.md", self.TODAY)

    def test_missing_profile(self):
        assert "no profile" in profile_status({}, None, self.TODAY)

    def test_bundled_example_is_not_a_checked_profile(self, tmp_path, monkeypatch):
        """dify and airflow had no profile of their own; the dated example passed as checked."""
        monkeypatch.setenv("OSS_PR_HOME", str(tmp_path))
        facts, _rules, path = load_profile("langgenius/dify")
        assert path is not None and "example" in profile_status(facts, path, self.TODAY)

    def test_draft_marker_survives_parsing(self):
        assert parse_facts("- checked: draft (auto, verify)\n")["checked"] == "draft"


def test_old_open_pr_beyond_a_full_mixed_search_is_still_seen(monkeypatch):
    """100 merged PRs filled the only search, so an older open PR never reached the cap or silence rules."""
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    merged = [{"number": i, "state": "MERGED", "createdAt": "2026-09-01T00:00:00Z", "labels": []} for i in range(100)]
    still_open = [{"number": 500, "state": "OPEN", "createdAt": "2026-06-01T00:00:00Z", "labels": []}]
    monkeypatch.setattr(pacing, "gh_json", lambda args: still_open if "open" in args else merged)
    monkeypatch.setattr(pacing, "responded", lambda repo, number, me: False)
    prs = pacing.my_prs("a/b", "me", timedelta(days=7), now)
    assert [p.number for p in prs if p.state == "OPEN"] == [500]
