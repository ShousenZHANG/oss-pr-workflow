"""Defects found by the acceptance dry runs on grafana, mlflow and cilium, each pinned by a test."""

import pytest
from _gh import base_changed_ranges
from base_rate import closing_comment
from contention_map import check, parse_check
from diff_check import check_commits, check_signatures, disclosure_trailer_regex
from pr_body_check import check_disclosure, check_references, check_template
from profile_draft import (
    Evidence,
    classify_line,
    disclosure_lines,
    disclosure_location,
    title_style,
    workflow_commands,
)


class TestPrBodyCheck:
    def test_inline_flag_regex_is_enforced(self):
        """`(?m)^From \\S+` was treated as an unfilled placeholder and the check skipped."""
        facts = {"disclosure_regex": r"(?m)^From \S+", "disclosure_location": "body"}
        assert check_disclosure("no disclosure", facts)[0].level == "FAIL"
        assert check_disclosure("Body\n\nFrom Claude Code\n", facts) == []

    def test_unfilled_and_not_required_and_invalid(self):
        assert check_disclosure("x", {"disclosure_regex": "FILL IN from the template"})[0].level == "FAIL"
        assert check_disclosure("x", {"disclosure_regex": "none required"}) == []
        assert check_disclosure("x", {"disclosure_regex": "(unclosed"})[0].level == "FAIL"

    def test_commit_trailer_disclosure_is_not_checked_in_the_body(self):
        facts = {"disclosure_regex": "Co-Authored-By: Claude", "disclosure_location": "commit-trailer"}
        assert check_disclosure("no disclosure in body", facts)[0].level == "INFO"

    def test_bold_headings_count(self):
        template = "**What is this feature?**\n\n**Why do we need it?**\n"
        findings = check_template("**What is this feature?**\nA fix.\n", template)
        assert [f.message for f in findings] == ["template heading missing: 'Why do we need it?'"]

    def test_template_instruction_text_left_in(self):
        template = "## Summary\n\n[Add information on how this was tested]\n"
        findings = check_template("## Summary\n\n[Add information on how this was tested]\n", template)
        assert any("instruction text left in" in f.message for f in findings)

    def test_checklist_item_with_appended_note_matches(self):
        template = "## C\n- [ ] Tests added\n"
        assert check_template("## C\n- [x] Tests added (two parametrized cases)\n", template) == []

    def test_template_backticks_are_not_reference_warnings(self):
        def repo_has(token, path):
            return False

        template = "- [ ] `area/tracking`\n- [ ] `rn/none`\n"
        findings = check_references("- [x] `area/tracking`\n- [ ] `rn/none`\n", [], "", repo_has, template)
        assert findings == []


class TestDiffCheck:
    def test_unsigned_commits_fail_when_signing_is_required(self):
        statuses = [("fix: a", "N"), ("fix: b", "G"), ("fix: c", "E")]
        levels = [f.level for f in check_signatures(statuses, required=True)]
        assert levels == ["FAIL", "WARN"]
        assert check_signatures(statuses, required=False) == []

    def test_required_trailer(self):
        facts = {"disclosure_regex": "Co-Authored-By: Claude", "disclosure_location": "commit-trailer"}
        regex = disclosure_trailer_regex(facts)
        assert any(f.level == "FAIL" for f in check_commits(["fix: a\n"], False, regex))
        assert check_commits(["fix: a\n\nCo-Authored-By: Claude <x@y>\n"], False, regex) == []

    def test_unrequested_trailer_is_a_warning(self):
        findings = check_commits(["fix: a\n\nCo-Authored-By: Claude <x@y>\n"], False, None)
        assert [f.level for f in findings] == ["WARN"]
        assert disclosure_trailer_regex({"disclosure_regex": "From \\S+", "disclosure_location": "body"}) is None


class TestContention:
    def test_ranges_exclude_hunk_context(self):
        patch = "@@ -10,7 +10,7 @@\n a\n b\n c\n-old\n+new\n d\n e\n f\n"
        assert base_changed_ranges(patch) == [(13, 13)]

    def test_insertion_after_a_line(self):
        assert base_changed_ranges("@@ -100,0 +101,1 @@\n+x\n") == [(101, 101)]

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("src/a.py:120,245", ("src/a.py", [120, 245])),
            ("src/a.py", ("src/a.py", [])),
            ("src\\a.py:7", ("src/a.py", [7])),
            ("C:/x/a.py", ("C:/x/a.py", [])),
        ],
    )
    def test_parse_check(self, spec, expected):
        assert parse_check(spec) == expected

    def test_new_file_in_other_pr(self, capsys):
        data = {
            "prs": {
                "9": {
                    "title": "add x",
                    "author": "a",
                    "updated_at": "2026-10-01",
                    "files": {"pkg/new.py": [[1, 1]]},
                    "added": ["pkg/new.py"],
                }
            }
        }
        check(data, "pkg/new.py", [5])
        assert "NEW FILE in that PR" in capsys.readouterr().out


class TestBaseRate:
    def comment(self, login, body, when, user_type="User"):
        return {"user": {"login": login, "type": user_type}, "body": body, "created_at": when}

    def test_closing_comment_prefers_people_and_skips_boilerplate(self):
        comments = [
            self.comment("maint", "Closing: duplicate of #12", "2026-10-01T10:00:00Z"),
            self.comment("ci-bot", "Install mlflow from this PR: pip install ...", "2026-10-01T10:05:00Z", "Bot"),
            self.comment("someone", "late comment", "2026-10-05T10:00:00Z"),
        ]
        chosen = closing_comment(comments, "2026-10-01T10:06:00Z")
        assert chosen["user"]["login"] == "maint"

    def test_bot_closure_reason_is_kept(self):
        comments = [
            self.comment("github-actions[bot]", "Closed because #5 is missing the ready label", "2026-10-01T10:00:00Z")
        ]
        assert "ready label" in closing_comment(comments, "2026-10-01T10:00:30Z")["body"]


class TestProfileDraft:
    def test_human_approval_gate_in_agent_doc(self):
        """grafana AGENTS.md: stop and get explicit human approval before pushing."""
        line = "Before running `git push`, stop and get explicit human approval."
        assert classify_line(line, agent_doc=True) == "human-in-loop"

    def test_co_authored_by_trailer_is_disclosure(self):
        """mlflow CLAUDE.md asks for a Co-Authored-By trailer when Claude Code authors a commit."""
        line = "- Co-Authored-By trailer: Include when Claude Code authors the commit"
        assert classify_line(line, agent_doc=True) == "disclosure"
        evidence = [Evidence("disclosure", "CLAUDE.md", 1, line)]
        assert disclosure_location(evidence) == "commit-trailer"

    def test_title_style(self):
        assert title_style(["Number formatting: Fix sci", "Alerting: Add x", "Dashboards: Fix y"], {}).startswith(
            "area-prefix"
        )
        assert title_style([], {"c.md": "Use the Conventional Commits format"}).startswith("conventional")
        assert title_style(["fix: a", "feat(x): b"], {}).startswith("conventional")

    def test_workflow_noise_is_dropped(self):
        text = (
            "steps:\n  - run: |\n      # set up\n      echo hi\n      make lint\n"
            "  - run: gh pr comment $PR --body x\n  - run: uv run pytest -q\n"
        )
        assert workflow_commands(text) == ["make lint", "uv run pytest -q"]

    def test_disclosure_lines_ignore_product_talk(self):
        bodies = ["Fixes the AI Gateway retry logic for LLM judges", "Generated-by: Claude Code following the rules"]
        assert disclosure_lines(bodies) == ["Generated-by: Claude Code following the rules"]
