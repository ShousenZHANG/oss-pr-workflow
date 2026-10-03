"""Unit tests for the pure parts of the scripts. No network: `gh` is never called."""

import pytest
from _gh import base_side_ranges, is_bot  # noqa: E402
from base_rate import ClosedPR, external_only, rate_line  # noqa: E402
from contention_map import distance, verdict  # noqa: E402
from issue_prs import pr_state  # noqa: E402
from pr_status import human, summarize_checks  # noqa: E402

PATCH = """@@ -10,7 +10,8 @@ def f():
 context
-old
+new
@@ -100 +101 @@
-x
+y
@@ -200,0 +202,3 @@
+added
"""


class TestBaseSideRanges:
    def test_parses_ranges_with_and_without_length(self):
        assert base_side_ranges(PATCH) == [(10, 16), (100, 100), (200, 200)]

    def test_empty_or_missing_patch(self):
        assert base_side_ranges("") == []
        assert base_side_ranges(None) == []


class TestVerdict:
    RANGES = [[10, 16], [100, 100]]

    @pytest.mark.parametrize(
        ("line", "expected"),
        [(12, 0), (16, 0), (20, 4), (5, 5), (60, 40), (101, 1)],
    )
    def test_distance(self, line, expected):
        assert distance(line, self.RANGES) == expected

    def test_distance_without_ranges_is_far(self):
        assert distance(5, []) >= 10**9

    @pytest.mark.parametrize(
        ("line", "prefix"),
        [(12, "HARD CONFLICT"), (26, "HARD CONFLICT"), (27, "HOT FILE"), (46, "HOT FILE"), (47, "DISTINCT")],
    )
    def test_bands_match_the_l3_table(self, line, prefix):
        assert verdict(line, [[10, 16]]).startswith(prefix)

    def test_missing_patch_is_unknown_not_distinct(self):
        assert verdict(12, None).startswith("UNKNOWN")


def _pr(number, author, association, merged, user_type="User"):
    return ClosedPR(number, author, association, user_type, merged, "2026-10-01T00:00:00Z", "t")


class TestBaseRate:
    def test_external_only_drops_members_and_bots(self):
        rows = [
            _pr(1, "alice", "CONTRIBUTOR", True),
            _pr(2, "maint", "MEMBER", True),
            _pr(3, "dependabot[bot]", "NONE", True),
            _pr(4, "bob", "FIRST_TIME_CONTRIBUTOR", False),
            _pr(5, "owner", "OWNER", False),
        ]
        assert [r.number for r in external_only(rows)] == [1, 4]

    def test_external_only_drops_user_account_bots_and_hidden_maintainers(self):
        rows = [
            _pr(1, "HaystackBot", "CONTRIBUTOR", True),
            _pr(2, "renovate", "NONE", True, user_type="Bot"),
            _pr(3, "employee", "CONTRIBUTOR", True),
            _pr(4, "outsider", "CONTRIBUTOR", False),
        ]
        assert [r.number for r in external_only(rows, maintainers={"employee"})] == [4]

    def test_rate_line(self):
        rows = [_pr(1, "a", "NONE", True), _pr(2, "b", "NONE", True), _pr(3, "c", "NONE", False)]
        line = rate_line("x", rows)
        assert "merged    2" in line and "closed-unmerged    1" in line and "66.7%" in line

    def test_rate_line_without_rows(self):
        assert "n/a" in rate_line("x", [])


class TestStatus:
    def test_summarize_checks(self):
        rollup = [
            {"status": "COMPLETED", "conclusion": "SUCCESS", "name": "a"},
            {"status": "COMPLETED", "conclusion": "FAILURE", "name": "lint"},
            {"status": "IN_PROGRESS", "conclusion": None, "name": "slow"},
            {"state": "SUCCESS", "context": "legacy-status"},
        ]
        counts, failed = summarize_checks(rollup)
        assert counts == {"SUCCESS": 2, "FAILURE": 1, "PENDING": 1}
        assert failed == ["lint"]

    @pytest.mark.parametrize(
        ("author", "expected"),
        [
            ({"login": "alice"}, True),
            ({"login": "github-actions[bot]"}, False),
            ({"login": "copilot-pull-request-reviewer"}, False),
            (None, False),
        ],
    )
    def test_human(self, author, expected):
        assert human(author) is expected

    @pytest.mark.parametrize(
        ("login", "user_type", "expected"),
        [
            ("renovate", "Bot", True),
            ("HaystackBot", "User", True),
            ("dependabot[bot]", "Bot", True),
            ("some-ci-bot", "User", True),
            ("alice", "User", False),
        ],
    )
    def test_is_bot(self, login, user_type, expected):
        assert is_bot(login, user_type) is expected

    def test_pr_state(self):
        assert pr_state({"merged_at": "2026-01-01", "state": "closed"}) == "MERGED"
        assert pr_state({"merged_at": None, "state": "closed"}) == "CLOSED"
        assert pr_state({"merged_at": None, "state": "open"}) == "OPEN"
