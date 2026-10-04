"""Gaps found by the 2026-10-04 audit of v1.0.0. Each test reproduces a case a gate let through."""

import json
import sys
from datetime import date, datetime, timedelta, timezone

import contention_map
import issue_prs
import pacing
import pr_status
import pytest
from _config import load_profile, parse_facts, profile_status, scope_globs, split_globs
from conftest import git, run
from contention_map import check, shift_ranges
from diff_check import added_lines, check_commits
from findings_check import changed_ranges, check_tests
from ledger import Entry, parse, render
from pacing import Limits, MyPR, decide
from pr_body_check import check_disclosure, check_template
from profile_draft import classify_ai_policy, ranked_commands, workflow_commands


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


class TestAiPolicyKeepsBans:
    def test_grammar_allowance_does_not_lift_a_code_ban(self):
        text = "We do not accept AI-generated code.\n\nAI tools are allowed to help with documentation grammar.\n"
        assert classify_ai_policy({"CONTRIBUTING.md": text})[0] == "banned"

    def test_lowercase_ban(self):
        assert classify_ai_policy({"CONTRIBUTING.md": "we do not accept ai-generated code.\n"})[0] == "banned"

    @pytest.mark.parametrize(
        "line",
        [
            "We do not accept AI-generated code you do not understand.",
            "PRs that are entirely AI-generated will be closed; do not submit them.",
            "Do not submit AI output without reviewing it yourself.",
            "We do not accept low-effort AI-generated pull requests.",
        ],
    )
    def test_conditional_ban_means_a_human_in_the_loop(self, line):
        assert classify_ai_policy({"CONTRIBUTING.md": line + "\n"})[0] == "human-in-loop"

    def test_general_allowance_with_partial_bans_is_human_in_loop(self):
        """home-assistant: AI welcome as an aid; autonomous agents and AI-written answers are not."""
        text = (
            "In short: AI tools are welcome as an aid, but you must fully understand and be\n"
            "able to explain every change you submit. Contributions made by autonomous\n"
            "agents are not accepted.\n\n"
            "**Do not use AI to generate answers to questions from maintainers.** Using AI to improve "
            "grammar or clarity is fine.\n"
        )
        level, evidence = classify_ai_policy({"CONTRIBUTING.md": text})
        assert level == "human-in-loop", evidence

    def test_continued_sentence_is_read_whole(self):
        text = "Contributions made by autonomous\nagents are not accepted.\n"
        assert classify_ai_policy({"CONTRIBUTING.md": text})[0] == "human-in-loop"

    def test_agents_file_forbidding_prs(self):
        """AGENTS.md said "Do not open pull requests." with no AI word and read as no policy."""
        assert classify_ai_policy({"AGENTS.md": "Do not open pull requests.\n"})[0] == "human-in-loop"


class TestSmallParsers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("notes/**", ["notes/**"]),
            ("normal/**, docs/**", ["normal/**", "docs/**"]),
            ("**/*.{ts,tsx}", ["**/*.{ts,tsx}"]),
            ("yes: **/*.{md,rst}, `notes/**`", ["**/*.{md,rst}", "notes/**"]),
            ("no", []),
            ("none found", []),
            ("False", []),
        ],
    )
    def test_scope_globs(self, value, expected):
        """`notes/**` started with "no" and switched the ASCII check off; brace lists were cut at the comma."""
        assert scope_globs(value) == expected

    def test_plan_with_brace_glob(self):
        assert split_globs("pkg/a.py,web/**/*.{ts,tsx}") == ["pkg/a.py", "web/**/*.{ts,tsx}"]

    def test_dco_needs_a_real_trailer(self):
        assert check_commits(["fix: mention Signed-off-by: in the docs"], dco=True)
        assert check_commits(["fix: a\n\nSigned-off-by: someone\n"], dco=True)
        assert check_commits(["fix: a\n\nSigned-off-by: A Person <a@example.com>\n"], dco=True) == []

    @pytest.mark.parametrize("field", ["basis", "notes"])
    def test_ledger_keeps_windows_newlines_in_one_row(self, field):
        """A CRLF in a middle field split the row and the whole entry vanished on the next read."""
        values = dict(id=1, date="2026-10-04", repo="a/b", target="#1", stage="recon", estimate="50%")
        values |= dict(basis="b", pr="", outcome="", notes="n")
        values[field] = "line one\r\nline two"
        rows = parse(render([Entry(**values)]))
        assert len(rows) == 1 and getattr(rows[0], field) == "line one line two"


class TestPrBodyReadsWhatReadersSee:
    TEMPLATE = "## Summary\n\n## Checklist\n\n- [ ] I added tests\n"
    FACTS = {"ai_policy": "disclosure", "disclosure_regex": "(?i)AI[- ]?assist"}

    def test_heading_named_in_prose_is_not_a_heading(self):
        body = "We list a Summary and a Checklist here.\n\n- [x] I added tests\n"
        messages = [f.message for f in check_template(body, self.TEMPLATE)]
        assert sum("heading missing" in m for m in messages) == 2, messages

    def test_real_headings_pass(self):
        body = "## Summary\n\nFix.\n\n### Checklist\n\n- [x] I added tests\n"
        assert check_template(body, self.TEMPLATE) == []

    def test_non_latin_checklist_items_are_compared(self):
        """normalize() deleted every CJK character, so any Chinese item matched any other."""
        template = "## 检查\n\n- [ ] 我已添加测试\n"
        body = "## 检查\n\n- [x] 我已更新文档\n"
        messages = [f.message for f in check_template(body, template)]
        assert any("missing or reworded" in m for m in messages), messages

    def test_disclosure_hidden_in_a_comment_does_not_count(self):
        body = "Fix the crash.\n\n<!-- AI-assisted -->\n"
        assert [f.level for f in check_disclosure(body, self.FACTS)] == ["FAIL"]
        assert check_disclosure("Fix the crash.\n\nAI-assisted with Claude Code.\n", self.FACTS) == []


class TestCiCommands:
    def test_pull_request_workflow_with_manual_trigger_is_kept(self):
        ci = "on:\n  pull_request:\n  workflow_dispatch:\njobs:\n  t:\n    steps:\n      - run: pytest -q\n"
        assert ranked_commands({".github/workflows/ci.yml": ci}) == ["pytest -q"]

    def test_list_form_trigger(self):
        ci = "on:\n  - push\n  - pull_request\njobs:\n  t:\n    steps:\n      - run: make lint\n"
        assert ranked_commands({".github/workflows/ci.yml": ci, ".github/workflows/x.yml": "on: push\n"}) == [
            "make lint"
        ]

    def test_release_only_workflow_is_still_dropped(self):
        release = "on:\n  push:\n    tags:\n      - 'v*'\njobs:\n  r:\n    steps:\n      - run: pytest -q\n"
        assert ranked_commands({".github/workflows/publish.yml": release}) == []

    def test_every_command_of_a_run_block(self):
        """Only the first line of a multi-line `run: |` was kept, so later checks never reached the profile."""
        text = "steps:\n  - run: |\n      ruff check .\n      pytest -q \\\n        --cov=src\n      echo done\n"
        assert workflow_commands(text) == ["ruff check .", "pytest -q --cov=src"]


class TestFailedLookupsAreShown:
    def test_status_with_failed_searches_is_not_zero(self, monkeypatch, capsys):
        """Both searches failed and the report said "0 open PRs" with a clean exit."""
        monkeypatch.setattr(pr_status, "gh_json", lambda args: None)
        monkeypatch.setattr(sys, "argv", ["pr_status.py", "--author", "me"])
        with pytest.raises(SystemExit) as exit_info:
            pr_status.main()
        out = capsys.readouterr().out
        assert exit_info.value.code == 1
        assert "ERROR: could not search your open PRs" in out and "0 open PRs" not in out, out

    def test_unread_referencing_pr_makes_the_rate_partial(self, monkeypatch, capsys):
        """One of two closed PRs could not be read and the rate came out as a complete 100%."""
        merged = {"merged_at": "2026-09-01T00:00:00Z", "state": "closed", "user": {"login": "x"}, "title": "t"}
        merged |= {"created_at": "2026-08-01T00:00:00Z", "closed_at": "2026-09-01T00:00:00Z", "body": "Fixes #5"}
        merged |= {"merged_by": {"login": "m"}}
        responses = {"repos/a/b/issues/5": {"state": "open", "title": "bug", "labels": []}, "repos/a/b/pulls/1": merged}
        monkeypatch.setattr(issue_prs, "gh_json", lambda args: responses.get(args[1]))
        monkeypatch.setattr(issue_prs, "referencing_prs", lambda repo, issue: [1, 2])
        monkeypatch.setattr(sys, "argv", ["issue_prs.py", "a/b", "5", "--no-comments"])
        with pytest.raises(SystemExit) as exit_info:
            issue_prs.main()
        out = capsys.readouterr().out
        assert exit_info.value.code == 1
        assert "#2 could not be read" in out and "partial" in out and "rate 100.0%" not in out, out


class TestContentionAcrossAMovedBase:
    """An open PR changed line 50; main then gained 100 lines at the top; your change is at line 150.

    The PR's line numbers are relative to where it branched, so 50 and 150 are the same line: a real
    merge conflict that the map called DISTINCT.
    """

    DATA = {"prs": {"7": {"title": "t", "author": "other", "updated_at": "2026-10-01", "files": {"a.py": [[50, 50]]}}}}
    INSERT_100 = "@@ -0,0 +1,100 @@\n" + "+x\n" * 100

    def test_ranges_follow_insertions_above_them(self):
        assert shift_ranges([[50, 50]], self.INSERT_100) == [[150, 150]]
        assert shift_ranges([[50, 60]], "@@ -10,2 +10,0 @@\n-a\n-b\n") == [[48, 58]]
        assert shift_ranges([[5, 5]], "@@ -70 +70 @@\n-a\n+b\n") == [[5, 5]]

    def test_lines_rewritten_on_the_base_cannot_be_mapped(self):
        assert shift_ranges([[50, 50]], "@@ -49,3 +49,3 @@\n-a\n-b\n-c\n+a\n+b\n+c\n") is None

    def test_check_maps_before_judging(self, capsys):
        check(self.DATA, "a.py", [150], drift=lambda number, pr, path: self.INSERT_100)
        out = capsys.readouterr().out
        assert "HARD CONFLICT" in out and "DISTINCT" not in out, out

    def test_unreadable_drift_is_unknown(self, capsys):
        check(self.DATA, "a.py", [150], drift=lambda number, pr, path: None)
        out = capsys.readouterr().out
        assert "UNKNOWN" in out and "DISTINCT" not in out, out

    def test_unchanged_file_keeps_the_old_answer(self, capsys):
        check(self.DATA, "a.py", [150], drift=lambda number, pr, path: "")
        assert "DISTINCT (100 lines away)" in capsys.readouterr().out


def test_base_drift_reads_one_compare_per_pr(monkeypatch):
    calls = []
    files = [
        {"filename": "a.py", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
        {"filename": "new.py", "previous_filename": "old.py"},
    ]

    def fake(args):
        calls.append(args)
        return {"files": files}

    monkeypatch.setattr(contention_map, "gh_json", fake)
    drift = contention_map.base_drift("o/r", compare_cap=3)
    pr = {"head_sha": "abc", "base_ref": "main"}
    assert drift("1", pr, "a.py").startswith("@@")
    assert drift("1", pr, "b.py") == ""
    assert drift("1", pr, "old.py") is None  # renamed without a patch: unknown
    assert len(calls) == 1 and calls[0][1] == "repos/o/r/compare/abc...main"
    assert contention_map.base_drift("o/r", compare_cap=2)("1", pr, "b.py") is None  # full list: unknown
    monkeypatch.setattr(contention_map, "gh_json", lambda args: None)
    assert contention_map.base_drift("o/r")("1", pr, "a.py") is None
