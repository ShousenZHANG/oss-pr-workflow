"""Config parsing, glob matching, and the heuristics in profile_draft.py. No network."""

import pytest
from _config import (
    config_int,
    config_list,
    glob_match,
    load_config,
    load_profile,
    parse_facts,
    parse_path_rules,
    scope_globs,
    truthy,
)
from profile_draft import (
    classify_ai_policy,
    classify_line,
    conventional_share,
    detect_open_pr_limit,
    forbids_ai_replies,
    is_policy_location,
    link_styles,
    percentile,
    workflow_commands,
)

PROFILE = """# acme/widgets

## Facts

- checked: 2026-10-03
- ai_policy: disclosure (auto, verify)
- open_pr_limit: 1
- dco: yes

## Path rules

| glob | rule |
|------|------|
| `api/controllers/**` | run the controller guard |
| `**/*.{ts,tsx}` | no default exports |

## Notes

- unrelated: value
"""


class TestGlob:
    @pytest.mark.parametrize(
        ("pattern", "path", "expected"),
        [
            ("api/controllers/**", "api/controllers/a/b.py", True),
            ("api/controllers/**", "api/services/b.py", False),
            ("**/*.py", "x.py", True),
            ("**/*.py", "a/b/x.py", True),
            ("*.py", "a/x.py", False),
            ("**/*.{ts,tsx}", "web/app/page.tsx", True),
            ("**/*.{ts,tsx}", "web/app/page.js", False),
            ("docs/?.md", "docs/a.md", True),
        ],
    )
    def test_glob_match(self, pattern, path, expected):
        assert glob_match(pattern, path) is expected

    def test_windows_separators(self):
        assert glob_match("api/**", "api\\x\\y.py")


class TestFacts:
    def test_facts_section_only_and_auto_suffix_stripped(self):
        facts = parse_facts(PROFILE, "Facts")
        assert facts == {"checked": "2026-10-03", "ai_policy": "disclosure", "open_pr_limit": "1", "dco": "yes"}

    def test_path_rules_table(self):
        assert parse_path_rules(PROFILE) == [
            ("api/controllers/**", "run the controller guard"),
            ("**/*.{ts,tsx}", "no default exports"),
        ]

    def test_config_defaults_and_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OSS_PR_HOME", str(tmp_path))
        assert config_int(load_config(), "per_repo_open_cap") == 3
        (tmp_path / "config.md").write_text("- per_repo_open_cap: 2\n- sources: issues\n", encoding="utf-8")
        config = load_config()
        assert config_int(config, "per_repo_open_cap") == 2
        assert config_list(config, "sources") == ["issues"]

    def test_user_profile_wins_over_example(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OSS_PR_HOME", str(tmp_path))
        (tmp_path / "repos").mkdir()
        (tmp_path / "repos" / "acme__widgets.md").write_text(PROFILE, encoding="utf-8")
        facts, rules, path = load_profile("acme/widgets")
        assert facts["dco"] == "yes" and len(rules) == 2 and path.name == "acme__widgets.md"

    def test_escaped_pipe_in_rule(self):
        rules = parse_path_rules("## Path rules\n\n| glob | rule |\n|---|---|\n| `a/**` | use `T \\| None` |\n")
        assert rules == [("a/**", "use `T | None`")]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("yes", True), ("yes (first-timers)", True), ("Required", True), ("no", False), ("", False), (None, False)],
    )
    def test_truthy(self, value, expected):
        assert truthy(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("no", []),
            ("yes", ["**"]),
            ("releasenotes/notes/**", ["releasenotes/notes/**"]),
            ("yes: docs/**, `*.rst`", ["docs/**", "*.rst"]),
        ],
    )
    def test_scope_globs(self, value, expected):
        assert scope_globs(value) == expected

    def test_missing_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OSS_PR_HOME", str(tmp_path))
        assert load_profile("nobody/nothing") == ({}, [], None)


class TestAiPolicy:
    @pytest.mark.parametrize(
        ("text", "agent_doc", "expected"),
        [
            ("We do not accept AI-generated contributions.", False, "banned"),
            ("First-time contributors must not use AI tools to open PRs.", False, "banned-for-newcomers"),
            ("We do not allow autonomous agents to contribute.", False, "human-in-loop"),
            ("Do not use AI on good first issues.", False, "issue-restricted"),
            ("If you used AI tools, disclose it in the PR description.", False, "disclosure"),
            ("Do not prepare an unsolicited external upstream pull request.", True, "issue-restricted"),
            ("Autonomous contributions are not accepted.", True, "human-in-loop"),
            ("Do not use print statements in library code.", True, None),
            ("Keep PR titles short and do not mirror state in React.", True, None),
            ("This project builds AI agents for you.", False, None),
        ],
    )
    def test_classify_line(self, text, agent_doc, expected):
        assert classify_line(text, agent_doc) == expected

    def test_strictest_level_wins(self):
        level, evidence = classify_ai_policy(
            {"CONTRIBUTING.md": "Disclose AI use in the PR.\n\nWe do not accept AI-generated code.\n"}
        )
        assert level == "banned"
        assert {e.level for e in evidence} == {"banned", "disclosure"}

    def test_ban_with_explicit_allowance_is_downgraded(self):
        text = "Using AI to improve grammar is fine.\n\nWe do not accept AI output you have not reviewed.\n"
        level, _ = classify_ai_policy({"AI_POLICY.md": text})
        assert level == "human-in-loop"

    def test_no_policy(self):
        assert classify_ai_policy({"CONTRIBUTING.md": "Run the tests.\n"}) == ("none", [])

    def test_ai_replies_rule(self):
        hit = forbids_ai_replies({"AI_POLICY.md": "Do not use AI to generate answers to questions from maintainers."})
        assert hit and hit[0] == "AI_POLICY.md"

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("AGENTS.md", True),
            (".github/CONTRIBUTING.md", True),
            ("docs/AI_POLICY.md", True),
            ("packages/ui/AGENTS.md", False),
        ],
    )
    def test_policy_location(self, path, expected):
        assert is_policy_location(path) is expected


class TestProfileHelpers:
    def test_open_pr_limit(self):
        assert detect_open_pr_limit({"C.md": "Contributors can have only one open pull request at a time."})[0] == "1"
        assert detect_open_pr_limit({"C.md": "You can have at most 5 open PRs."})[0] == "5"
        assert detect_open_pr_limit({"C.md": "Thanks!"})[0] == "none found"

    def test_workflow_commands(self):
        text = "steps:\n  - run: make lint\n  - name: t\n    run: |\n      uv run pytest -q\n      echo done\n"
        assert workflow_commands(text) == ["make lint", "uv run pytest -q"]

    def test_link_styles(self):
        bodies = ["Fixes #12", "Refs #3", "part of #9", "no link"]
        assert link_styles(bodies) == {"closing": 1, "reference": 2, "none": 1}

    def test_conventional_share(self):
        assert conventional_share(["fix: a", "feat(api): b", "Add thing"]) == pytest.approx(2 / 3)

    def test_percentile(self):
        assert percentile([1, 2, 3, 4, 100], 0.9) == 100
        assert percentile([], 0.9) == 0


def test_notes_below_settings_do_not_override_them(tmp_path, monkeypatch):
    """First real use: a `- sources: ...` line in the notes replaced the real setting."""
    monkeypatch.setenv("OSS_PR_HOME", str(tmp_path))
    (tmp_path / "config.md").write_text(
        "- sources: issues\n\nNotes:\n\n- sources: `issues` = existing issues only\n", encoding="utf-8"
    )
    assert load_config()["sources"] == "issues"
