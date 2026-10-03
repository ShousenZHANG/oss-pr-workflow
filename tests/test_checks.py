"""Pure parts of diff_check, pr_body_check, findings_check, pacing and ledger. No network, no git."""

from datetime import datetime, timedelta, timezone

import pytest
from diff_check import added_lines, check_added, check_commits, check_files, check_size
from findings_check import changed_ranges, validate
from ledger import Entry, calibration, parse, render
from pacing import Limits, MyPR, decide
from pr_body_check import check_links, check_numbers, check_placeholders, check_references, check_template

DIFF = """diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -10,0 +11,2 @@ def f():
+    if not items:
+        raise ValueError("empty — no items")
diff --git a/pkg/new.py b/pkg/new.py
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,1 @@
+TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
"""


class TestDiffCheck:
    def test_added_lines_tracks_new_line_numbers(self):
        added = added_lines(DIFF)
        assert added["pkg/a.py"] == [(11, "    if not items:"), (12, '        raise ValueError("empty — no items")')]
        assert added["pkg/new.py"][0][0] == 1

    def test_secret_and_ascii(self):
        findings = check_added(added_lines(DIFF), ["pkg/a.py"], allow_generated=False)
        messages = [f.message for f in findings if f.level == "FAIL"]
        assert any("GitHub token" in m for m in messages)
        assert any("non-ASCII" in m and "pkg/a.py:12" in m for m in messages)

    def test_ascii_not_required(self):
        findings = check_added(added_lines(DIFF), [], allow_generated=False)
        assert not any("non-ASCII" in f.message for f in findings)

    def test_unplanned_file_and_lock_file(self):
        findings = check_files(["pkg/a.py", "pkg/extra.py", "web/package-lock.json"], ["pkg/a.py"], False)
        assert [f.level for f in findings if "extra" in f.message] == ["FAIL"]
        assert any(f.level == "WARN" and "lock file" in f.message for f in findings)

    def test_dco_and_ai_trailer(self):
        findings = check_commits(["fix: a\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"], dco=True)
        assert any(f.level == "FAIL" and "Signed-off-by" in f.message for f in findings)
        assert any(f.level == "WARN" and "AI attribution" in f.message for f in findings)
        assert check_commits(["fix: a\n\nSigned-off-by: A <a@b.c>\n"], dco=True) == []

    def test_size_envelope(self):
        facts = {"merged_pr_files_p90": "4", "merged_pr_lines_p90": "120"}
        assert len(check_size(9, 500, facts)) == 2
        assert check_size(2, 50, facts) == []
        assert check_size(9, 500, {}) == []


TEMPLATE = """## Summary

<!-- describe -->

## Checklist

- [ ] I added tests
- [ ] I ran the linters
"""


class TestPrBodyCheck:
    def test_complete_body_passes(self):
        body = "## Summary\n\nFix crash.\n\n## Checklist\n\n- [x] I added tests\n- [x] I ran the linters\n"
        assert check_template(body, TEMPLATE) == []

    def test_missing_heading_and_dropped_item(self):
        body = "Fix crash.\n\n- [x] I added tests\n"
        messages = [f.message for f in check_template(body, TEMPLATE)]
        assert any("heading missing" in m and "Summary" in m for m in messages)
        assert any("ran the linters" in m for m in messages)

    def test_invented_item(self):
        body = (
            "## Summary\nx\n## Checklist\n- [x] I added tests\n- [x] I ran the linters\n- [x] I updated the changelog\n"
        )
        assert any("invented" in f.message for f in check_template(body, TEMPLATE))

    def test_links(self):
        assert check_links("Refs #42", 42, umbrella=True, issue_required=True) == []
        assert check_links("Fixes #42", 42, umbrella=True, issue_required=True)[0].level == "FAIL"
        assert check_links("no link", None, umbrella=False, issue_required=True)[0].level == "FAIL"
        assert check_links("Fixes #421", 42, umbrella=False, issue_required=False)[0].message.startswith("issue #42")

    def test_placeholders(self):
        assert len(check_placeholders("Fixes #<issue number>\nGenerated-by: [Tool Name]")) == 2
        assert check_placeholders("<!-- TODO in a comment is fine -->") == []

    def test_references(self):
        def repo_has(token, path):
            return token in {"pkg/b.py", "helper"}

        findings = check_references(
            "Changes `pkg/a.py` and `pkg/b.py`; calls `helper()` and `ghost_fn`.", ["pkg/a.py"], "def helper", repo_has
        )
        assert [f.message for f in findings] == ["identifier `ghost_fn` appears nowhere in the diff or the repository"]

    def test_numbers(self):
        findings = check_numbers("It used to return 500, now 422. See #1234 and v2.10.1.", "status=422", "")
        assert [f.message.split()[1] for f in findings] == ["500"]


class TestFindingsCheck:
    FILES = ["pkg/a.py", "docs/x.md"]
    RANGES = {"pkg/a.py": [(11, 12)], "docs/x.md": [(1, 3)]}
    COUNTS = {"pkg/a.py": 50, "docs/x.md": 3}

    def report(self, **overrides):
        base = {
            "coverage": [
                {"path": "pkg/a.py", "status": "reviewed"},
                {"path": "docs/x.md", "status": "skipped", "reason": "docs only"},
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
                    "start_line": 11,
                    "end_line": 12,
                    "severity": "medium",
                    "category": "bug",
                    "evidence": "x",
                    "resolution": "fixed",
                }
            ],
        }
        base.update(overrides)
        return base

    LOGS = {
        "without.txt": "tests/test_a.py::test_empty FAILED\nAssertionError: expected ValueError\n1 failed",
        "with.txt": "tests/test_a.py::test_empty PASSED\n1 passed in 0.01s",
    }

    def levels(self, report, logs=None):
        logs = self.LOGS if logs is None else logs
        results = validate(report, self.FILES, self.RANGES, self.COUNTS, read_text=logs.get)
        return [r.level for r in results if r.level != "INFO"]

    def test_clean_report(self):
        assert self.levels(self.report()) == []

    def test_test_without_logs_fails(self):
        report = self.report(tests=[{"name": "test_empty", "fails_without_fix": True}])
        assert "FAIL" in self.levels(report)

    def test_without_fix_log_must_show_failure(self):
        logs = {"without.txt": "test_empty PASSED", "with.txt": self.LOGS["with.txt"]}
        assert "FAIL" in self.levels(self.report(), logs)

    def test_with_fix_log_must_be_clean(self):
        logs = {"without.txt": self.LOGS["without.txt"], "with.txt": "test_empty PASSED\n1 failed, 3 passed"}
        assert "FAIL" in self.levels(self.report(), logs)

    def test_log_without_the_test_name_is_a_warning(self):
        logs = {"without.txt": "FAILED something_else\n1 failed", "with.txt": self.LOGS["with.txt"]}
        assert self.levels(self.report(), logs) == ["WARN"]

    def test_missing_coverage(self):
        assert "FAIL" in self.levels(self.report(coverage=[{"path": "pkg/a.py", "status": "reviewed"}]))

    def test_line_outside_file(self):
        bad = dict(self.report()["findings"][0], start_line=80, end_line=81)
        assert "FAIL" in self.levels(self.report(findings=[bad]))

    def test_line_outside_hunk_is_warning(self):
        far = dict(self.report()["findings"][0], start_line=40, end_line=40)
        assert self.levels(self.report(findings=[far])) == ["WARN"]

    def test_protected_category_needs_test(self):
        item = dict(self.report()["findings"][0], category="behavior-change", resolution="dismissed: callers ok")
        assert "FAIL" in self.levels(self.report(findings=[item]))
        item = dict(item, resolution="dismissed: test: test_old_status_kept passes")
        assert self.levels(self.report(findings=[item])) == []

    def test_changed_ranges(self):
        diff = "+++ b/pkg/a.py\n@@ -10,0 +11,2 @@\n+a\n+b\n+++ b/pkg/c.py\n@@ -1 +1 @@\n-x\n+y\n"
        assert changed_ranges(diff) == {"pkg/a.py": [(11, 12)], "pkg/c.py": [(1, 1)]}


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
LIMITS = Limits(repo_limit=None, per_repo_cap=3, min_interval=timedelta(hours=24), silent_after=timedelta(days=7))


def pr(number, state="OPEN", age=timedelta(days=1), labels=(), responded=None):
    return MyPR(number, state, NOW - age, tuple(labels), responded)


class TestPacing:
    def test_go(self):
        assert decide([pr(1, age=timedelta(days=2))], LIMITS, NOW)[0][0] == "OK"

    def test_cap(self):
        prs = [pr(i, age=timedelta(days=2)) for i in range(3)]
        assert any("your cap" in reason for status, reason in decide(prs, LIMITS, NOW) if status == "STOP")

    def test_silence_pause(self):
        prs = [pr(1, age=timedelta(days=9), responded=False)]
        assert any("no response" in reason for _, reason in decide(prs, LIMITS, NOW))
        prs = [pr(1, age=timedelta(days=9), responded=True)]
        assert decide(prs, LIMITS, NOW)[0][0] == "OK"

    def test_flag_freeze_and_recovery(self):
        flagged = pr(1, state="CLOSED", age=timedelta(days=20), labels=["AI Spam"])
        assert decide([flagged], LIMITS, NOW)[0][0] == "STOP"
        recovered = pr(2, state="MERGED", age=timedelta(days=10))
        assert decide([flagged, recovered], LIMITS, NOW)[0][0] == "OK"


class TestLedger:
    def entry(self, i, estimate, outcome):
        return Entry(i, "2026-10-03", "a/b", f"#{i}", "ship", estimate, "basis", "", outcome, "")

    def test_roundtrip(self):
        entries = [self.entry(1, "70%", "merged"), self.entry(2, "40%", "")]
        assert parse(render(entries)) == entries

    def test_pipe_in_cell_is_escaped(self):
        entries = [Entry(1, "d", "a/b", "#1 a|b", "s", "50%", "x|y", "", "", "")]
        assert parse(render(entries))[0].target == "#1 a/b"

    def test_calibration_waits_for_twenty(self):
        assert calibration([self.entry(i, "70%", "merged") for i in range(1, 5)])[0].startswith("4 decided")

    @pytest.mark.parametrize("outcome_merged", [True, False])
    def test_calibration_flags_overestimates(self, outcome_merged):
        outcome = "merged" if outcome_merged else "closed-process"
        lines = calibration([self.entry(i, "90%", outcome) for i in range(1, 21)])
        assert lines[0].startswith("20 decided")
        assert ("too high" in "\n".join(lines)) is (not outcome_merged)
