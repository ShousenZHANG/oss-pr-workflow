"""Regression cases R1-R10: each is a real failure from past contribution rounds and the
gate that must now catch it. If one of these fails, a past mistake can happen again.
"""

import inspect
import re
from datetime import datetime, timedelta, timezone

import base_rate
from _gh import truncation_warning
from contention_map import check
from findings_check import validate
from issue_prs import issue_warning, kind_matches, link_kind
from pacing import Limits, MyPR, decide
from profile_draft import classify_ai_policy

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
LIMITS = Limits(repo_limit=None, per_repo_cap=3, min_interval=timedelta(hours=24), silent_after=timedelta(days=7))


def test_r1_ai_ban_stops_at_profile():
    """A repo was ranked first before anyone read its AI policy (numba, transformers)."""
    banned, _ = classify_ai_policy({"CONTRIBUTING.md": "We do not accept AI-generated pull requests.\n"})
    newcomers, _ = classify_ai_policy(
        {"CONTRIBUTING.md": "We ask that first-time contributors do not use code agents to create issues or PRs.\n"}
    )
    assert banned == "banned"
    assert newcomers == "banned-for-newcomers"


def test_r2_truncated_list_is_not_complete():
    """`gh pr list --limit N` returned exactly N rows and an existing PR (#38334) was missed."""
    assert truncation_warning(100, 100, "the open-PR search") is not None
    assert truncation_warning(37, 100, "the open-PR search") is None


def test_r3_campaign_rate_counts_only_same_kind_of_change():
    """Three PRs were filed under an umbrella whose merged PRs all removed `db.session`; theirs did not."""
    kind = re.compile(r"^-.*\bdb\.session\b", re.MULTILINE)
    merged_slice = "@@ -1,2 +1,2 @@\n-    rows = db.session.query(App).all()\n+    rows = session.query(App).all()\n"
    off_scope = (
        "@@ -1 +1 @@\n-    maker = sessionmaker(bind=db.engine)\n+    maker = session_factory.get_session_maker()\n"
    )
    assert kind_matches(merged_slice, kind)
    assert not kind_matches(off_scope, kind)


def test_r4_merge_rate_never_uses_the_search_api():
    """Search-based rates read 87% and 91% where the REST pull list gave 49.7% and 69.8%."""
    source = inspect.getsource(base_rate.collect_closed)
    assert "pulls?state=closed" in source
    assert "search/issues" not in inspect.getsource(base_rate)


def test_r5_repo_open_pr_limit_blocks_a_second_pr():
    """haystack allows one open PR per community contributor."""
    limits = Limits(1, 3, timedelta(hours=24), timedelta(days=7))
    open_one = [MyPR(1, "OPEN", NOW - timedelta(days=2), (), None)]
    assert any("repo limit" in reason for status, reason in decide(open_one, limits, NOW) if status == "STOP")


def test_r6_burst_of_prs_is_blocked():
    """17 PRs opened within 2 minutes were closed together as AI spam."""
    just_opened = [MyPR(1, "OPEN", NOW - timedelta(minutes=2), (), None)]
    assert any("wait" in reason for status, reason in decide(just_opened, LIMITS, NOW) if status == "STOP")


def _report(tests):
    return {
        "coverage": [{"path": "pkg/a.py", "status": "reviewed"}],
        "tests": tests,
        "findings": [],
    }


def test_r7_tautological_test_is_rejected():
    """Tests whose expected value came from the same constant as the code could never fail."""
    results = validate(_report([{"name": "test_value", "fails_without_fix": False}]), ["pkg/a.py"], {}, {})
    assert any(r.level == "FAIL" and "failing without the fix" in r.message for r in results)


def test_r8_closed_umbrella_is_flagged():
    """A test-only slice was opened under an umbrella that had already been closed (#72986)."""
    assert issue_warning({"state": "closed", "state_reason": "completed"}) is not None
    assert issue_warning({"state": "open"}) is None


def test_r9_no_op_change_cannot_pass_review():
    """A field was 'added' that a helper already injected; the diff changed nothing (#70638).

    A no-op has no test that fails before the change, so the same gate as R7 stops it.
    """
    results = validate(_report([]), ["pkg/a.py"], {}, {})
    assert any(r.level == "FAIL" for r in results)


def test_r10_own_prs_on_the_same_lines_are_flagged(capsys):
    """Two of our own PRs edited the same config line and conflicted as soon as one merged."""
    data = {
        "prs": {
            "101": {
                "title": "first",
                "author": "Me",
                "updated_at": "2026-10-01",
                "draft": False,
                "files": {"pyproject.toml": [[10, 12]]},
            }
        }
    }
    check(data, "pyproject.toml", [11], me="me")
    out = capsys.readouterr().out
    assert "YOUR OWN PR" in out and "HARD CONFLICT" in out


def test_closing_keyword_detection_matches_dedup_rule():
    """dify deduplicates PRs that close an issue; references survive (Fixes vs Refs)."""
    assert link_kind("Fixes #36544", 36544) == "closing"
    assert link_kind("Refs #36544", 36544) == "reference"
