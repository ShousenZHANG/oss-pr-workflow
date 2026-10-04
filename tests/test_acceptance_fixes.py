"""Defects found by the acceptance dry runs on grafana, mlflow and cilium, each pinned by a test."""

import pytest
from _config import glob_match
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


class TestCiliumFindings:
    def test_dco_wording_with_curly_apostrophe(self):
        from profile_draft import DCO_TEXT

        assert DCO_TEXT.search("All commits are signed off. See the section Developer’s Certificate of Origin")

    def test_policy_links_followed_across_repos(self):
        from profile_draft import policy_links

        text = (
            "Read our [AI policy](https://github.com/cilium/community/blob/main/AI-POLICY.md) and the "
            "[contributing guide](../Documentation/contributing/guide.rst). See [build docs](build.md)."
        )
        links = policy_links(".github/pull_request_template.md", text)
        assert ("cilium/community", "main::AI-POLICY.md") in links
        assert ("", "Documentation/contributing/guide.rst") in links
        assert all("build.md" not in path for _, path in links)

    def test_vendored_agent_files_are_not_path_rules(self):
        from profile_draft import scoped_agent_files

        tree = ["vendor/github.com/aws/smithy-go/AGENTS.md", "pkg/AGENTS.md", "third_party/x/CLAUDE.md"]
        assert scoped_agent_files(tree) == ["pkg/AGENTS.md"]

    def test_ai_written_pr_text_forbidden(self):
        from profile_draft import forbids_ai_text

        docs = {"AI-POLICY.md": "The PR description must not be written by generative AI tools."}
        assert forbids_ai_text(docs)

    def test_ail_disclosure_lines(self):
        assert disclosure_lines(["This PR was prepared with AIL:3"]) == ["This PR was prepared with AIL:3"]

    def test_release_note_block_and_attestations(self):
        from pr_body_check import check_attestations, check_release_block

        template = "Description\n\n```release-note\n<!-- note -->\n```\n"
        assert check_release_block("no block", template)[0].level == "FAIL"
        assert check_release_block("```release-note\nFix panic\n```", template) == []
        warns = check_attestations("- [x] I have read the contributing guide\n- [x] Tests pass\n")
        assert len(warns) == 1 and "contributing guide" in warns[0].message

    def test_test_waiver_needs_user_approval(self):
        from findings_check import validate

        report = {
            "coverage": [{"path": "a.go", "status": "reviewed"}],
            "tests": [],
            "findings": [],
            "test_waiver": {"precedent": "#41600", "ci_coverage": "e2e job", "approved_by_user": True},
        }
        levels = [r.level for r in validate(report, ["a.go"], {}, {"a.go": 10}) if r.level != "INFO"]
        assert levels == ["WARN"]
        report["test_waiver"]["approved_by_user"] = False
        assert "FAIL" in [r.level for r in validate(report, ["a.go"], {}, {"a.go": 10})]

    def test_ai_closure_detection(self):
        from base_rate import AI_CLOSURE

        assert AI_CLOSURE.search("Please don't get your AI to generate patches for open issues like this")
        assert AI_CLOSURE.search("closing: this looks like AI-generated slop")
        assert not AI_CLOSURE.search("Fixes the AI Gateway timeout")


def test_workflow_scripts_next_to_workflows_are_read():
    """mlflow's PR auto-close rules live in .github/workflows/auto-close-pr.js (second acceptance run)."""
    from profile_draft import automation_paths

    tree = [".github/workflows/ci.yml", ".github/workflows/auto-close-pr.js", ".github/scripts/x.py", "src/a.js"]
    workflows, scripts = automation_paths(tree)
    assert workflows == [".github/workflows/ci.yml"]
    assert scripts == [".github/workflows/auto-close-pr.js", ".github/scripts/x.py"]


class TestRoundTwoFindings:
    """Defects found by the second acceptance round (cilium, grafana, mlflow)."""

    def test_pacing_enforces_the_ai_gate(self):
        from pacing import ai_gate

        assert ai_gate("banned", None)[0] == "STOP"
        assert ai_gate("banned-for-newcomers (auto, verify)", 0)[0] == "STOP"
        assert ai_gate("banned-for-newcomers", None)[0] == "STOP"
        assert ai_gate("banned-for-newcomers", 3) is None
        assert ai_gate("disclosure", 0) is None

    def test_pacing_fails_closed_when_the_search_fails(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        import pacing

        monkeypatch.setattr(pacing, "gh_json", lambda args: None)
        with pytest.raises(RuntimeError):
            pacing.my_prs("a/b", "me", timedelta(days=7), datetime.now(timezone.utc))

    def test_identical_or_zero_failure_logs_are_not_proof(self):
        from findings_check import check_tests

        test = {"name": "test_x", "fails_without_fix": True, "without_fix_log": "a", "with_fix_log": "b"}
        same = {"a": "test_x passed\n1 passed", "b": "test_x passed\n1 passed"}
        assert any("identical" in f.message for f in check_tests([test], same.get))
        zero = {"a": "test_x\nℹ fail 0\nℹ pass 1", "b": "test_x ok\n1 passed"}
        assert any("shows no failure" in f.message for f in check_tests([test], zero.get))
        real = {"a": "FAIL test_x\nexpected 1, got 2\n1 failed", "b": "test_x ok\n1 passed"}
        assert [f.level for f in check_tests([test], real.get)] == []

    def test_not_run_needs_the_users_choice_and_waiver_never_covers_security(self):
        from findings_check import validate

        base = {
            "coverage": [{"path": "a.go", "status": "reviewed"}],
            "tests": [],
            "test_waiver": {"precedent": "#1", "ci_coverage": "e2e", "approved_by_user": True},
            "not_run": [{"check": "go test", "reason": "no Go"}],
            "findings": [
                {
                    "path": "a.go",
                    "start_line": 1,
                    "end_line": 1,
                    "severity": "high",
                    "category": "security",
                    "evidence": "x",
                    "resolution": "dismissed: input is trusted",
                }
            ],
        }
        messages = [r.message for r in validate(base, ["a.go"], {"a.go": [(1, 1)]}, {"a.go": 5}) if r.level == "FAIL"]
        assert any("user_choice" in m for m in messages)
        assert any("security can only be dismissed with a test" in m for m in messages)

    def test_finding_spanning_adjacent_hunks_is_inside(self):
        from findings_check import merge_near

        assert merge_near([(59, 61), (64, 66)]) == [(59, 66)]
        assert merge_near([(1, 2), (20, 21)]) == [(1, 2), (20, 21)]

    def test_reference_link_labels_are_not_instructions(self):
        template = (
            "- [ ] Read [Submitting a pull request] first\n\nSee [Submitting a pull request].\n\n"
            "[Submitting a pull request]: https://docs.example.io/contributing/submitting/\n"
        )
        body = "- [x] Read [Submitting a pull request] first\n\nSee [Submitting a pull request].\n"
        assert not any("instruction text" in f.message for f in check_template(body, template))

    def test_release_block_rules(self):
        from pr_body_check import check_release_block

        template = "```release-note\n<!-- one line; remove this release-note section if not needed -->\n```\n"
        empty = "```release-note\n<!-- one line -->\n```\n"
        assert check_release_block(empty, template)[0].level == "FAIL"
        assert check_release_block("no block, removal allowed", template) == []

    def test_verification_claims_need_confirmation(self):
        from pr_body_check import check_attestations

        warns = check_attestations("- [x] It works as expected from a user's perspective\n- [x] Docs\n")
        assert len(warns) == 1 and "confirm it was actually done" in warns[0].message

    def test_closing_comment_prefers_the_closer_and_ignores_welcome_bots(self):
        comments = [
            {"user": {"login": "author"}, "body": "I signed the CLA", "created_at": "2026-10-01T09:00:00Z"},
            {
                "user": {"login": "stale-bot", "type": "Bot"},
                "body": "Closing: no activity",
                "created_at": "2026-10-01T10:00:00Z",
            },
        ]
        assert closing_comment(comments, "2026-10-01T10:00:05Z", "stale-bot")["user"]["login"] == "stale-bot"
        welcome = [
            {"user": {"login": "welcome-bot", "type": "Bot"}, "body": "Welcome!", "created_at": "2026-10-01T08:00:00Z"}
        ]
        assert closing_comment(welcome, "2026-10-02T00:00:00Z", "maintainer") is None

    @pytest.mark.parametrize(
        "text",
        [
            "we can't accept patches co-authored by Copilot",
            "please don't AI-generate solutions for open issues",
            "This description is not human-written",
        ],
    )
    def test_more_ai_closure_phrasings(self, text):
        from base_rate import AI_CLOSURE

        assert AI_CLOSURE.search(text)

    def test_docs_site_links_map_to_repo_files(self):
        from profile_draft import docs_site_path

        tree = {"Documentation/contributing/development/contributing_guide.rst", "README.md"}
        url = "https://docs.cilium.io/en/stable/contributing/development/contributing_guide/#submitting"
        assert docs_site_path(url, tree) == "Documentation/contributing/development/contributing_guide.rst"

    def test_run_blocks_stop_at_dedent_and_checks_rank_first(self):
        from profile_draft import ranked_commands

        workflow = (
            "on: pull_request\njobs:\n  a:\n    steps:\n      - run: |\n          echo hi\n"
            "      - name: Setup Go\n        uses: x\n      - run: make release-notes\n      - run: make lint\n"
        )
        assert workflow_commands(workflow) == ["make release-notes", "make lint"]
        assert ranked_commands({".github/workflows/ci.yml": workflow})[0] == "make lint"

    def test_backports_are_not_sampled_and_code_by_user_is_detected(self):
        from profile_draft import CODE_BY_USER, is_sample_candidate

        pr = {"merged_at": "x", "author_association": "CONTRIBUTOR", "user": {"login": "a"}, "title": "v1.20 Backports"}
        assert not is_sample_candidate(pr, set())
        assert is_sample_candidate(dict(pr, title="fix: a"), set())
        assert CODE_BY_USER.search("AI can help you learn, but write the actual code yourself.")

    def test_workflow_artifacts_are_not_changed_files(self):
        from rules_for import WORKFLOW_ARTIFACT

        assert WORKFLOW_ARTIFACT.search("review.json") and WORKFLOW_ARTIFACT.search("runs/with_fix.txt")
        assert not WORKFLOW_ARTIFACT.search("src/review_json.py")


class TestRoundTwoMlflow:
    def test_removable_checkboxes(self):
        template = "<!-- Remove unused checkboxes -->\n## Type\n- [ ] Bug fix\n- [ ] Feature\n"
        assert check_template("## Type\n- [x] Bug fix\n", template) == []
        invented = check_template("## Type\n- [x] Refactor\n", template)
        assert any("invented" in f.message for f in invented)

    def test_check_commands_include_pytest_and_pre_commit(self):
        from profile_draft import CHECK_COMMAND, NOISE_COMMAND

        assert CHECK_COMMAND.search("pytest dev/clint dev/pypi")
        assert CHECK_COMMAND.search("pre-commit run --all-files")
        for noise in ("LOGIN=foo", "check_user() {", "**If you are cherry-picking**", 'title="Weekly report"'):
            assert NOISE_COMMAND.match(noise), noise

    def test_bot_rules_are_capped_per_file(self):
        from profile_draft import BOT_RULE, all_matches

        docs = {"a.yml": "\n".join(["auto-close"] * 20), "b.js": "const PROTECTED_PATHS = ['AGENTS.md']"}
        hits = all_matches(BOT_RULE, docs, 60)
        assert sum(1 for h in hits if h[0] == "a.yml") == 6
        assert any(h[0] == "b.js" for h in hits)
        assert hits[-1][0] == "..."

    def test_stale_timers_prefer_pr_keys_and_read_disabled(self):
        from profile_draft import stale_days

        text = {"stale.yml": "days-before-stale: 365\ndays-before-pr-stale: -1\n", "s.js": "const STALE_DAYS = 30;"}
        result = stale_days(text)
        assert "days-before-pr-stale disabled" in result and "365" not in result and "STALE_DAYS = 30" in result

    def test_continuation_line_does_not_make_evidence(self):
        from profile_draft import classify_ai_policy

        docs = {
            "CLAUDE.md": "- DCO sign-off: All commits MUST use the -s flag.\n- Disclose Claude Code use in a trailer.\n"
        }
        _level, evidence = classify_ai_policy(docs)
        assert all("DCO" not in e.text for e in evidence)

    def test_glob_alternatives_with_wildcards(self):
        assert glob_match("{src/*.py,**/test_*.py}", "a/b/test_x.py")
        assert glob_match("{src/*.py,**/test_*.py}", "src/m.py")
        assert not glob_match("{src/*.py,**/test_*.py}", "lib/m.py")
