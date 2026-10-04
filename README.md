# oss-pr-workflow

English | [简体中文](README.zh-CN.md)

A Claude Code plugin that takes any GitHub repository from "scan the issues" to "the PR merged", with a scripted checkpoint between every phase, so that what you submit is not rejected for reasons you could have seen in advance.

In busy repositories most rejected PRs are not rejected for code quality. They die because the issue was already taken, another PR was rewriting the same lines, the repository keeps one PR per issue, it bans AI-assisted work, or a disclosure or release-note rule was missed. Each phase here catches one family of those failures.

## Phases

| # | Skill | What it does | Gate before the next phase |
|---|---|---|---|
| 0 | `oss-pr` | Optional: list candidate repositories with merge facts and AI policy | You pick one |
| 1 | `oss-pr-profile` | Drafts the repository's rules from its own documents and merged PRs; you check them | AI policy allows you to contribute; profile checked |
| 2 | `oss-pr-scout` | Finds up to five targets in existing issues, umbrella issues and recently merged code | You pick one |
| 3 | `oss-pr-recon` | Proves the target is free (issue, files, lines, governance) and estimates the odds | All hard gates pass; you say go |
| 4 | `oss-pr-build` | Implements in a separate worktree, test first, reproduces CI, independent review | Test seen failing without the fix; every file reviewed; checks pass |
| 5 | `oss-pr-ship` | Paces, drafts and checks the PR text, opens it after your approval, follows up | Pacing allows it; you approve the exact text |

**Nothing anyone else can see happens without your approval of that exact text**: comments, issues, PRs, review replies, pings. Pushing to your own fork is the only automatic network write.

## Scripts

Twelve standard-library Python scripts in `skills/oss-pr/scripts/` do every measurement that must not be wrong. Each has `--help`.

| Script | Answers |
|---|---|
| `pick_repo.py` | Which repositories in a language accept outside PRs, and their AI policy |
| `profile_draft.py` | A draft profile: AI policy level with quotes, disclosure, limits, DCO/CLA, CI commands, merged-PR size |
| `base_rate.py` | Outside merge rate, split into newcomers and returning contributors, why PRs were closed, your own status |
| `issue_prs.py` | Every PR that ever referenced an issue, its link keyword, the issue's state; `--diff-match` for same-kind rates |
| `contention_map.py` | Which files and line ranges every open PR touches; your own PRs flagged |
| `rules_for.py` | The profile's path rules (and `ocr` rules if installed) for the files you changed |
| `diff_check.py` | Unplanned files, secrets, lock/generated files, scoped ASCII rules, DCO sign-off, size envelope |
| `findings_check.py` | Self-review report: every file covered, line numbers real, findings resolved, a test that fails without the fix |
| `pacing.py` | Whether you may open another PR in this repository now |
| `pr_body_check.py` | PR text against the template, the profile and your diff |
| `pr_status.py` | Your open PRs: CI, reviews, new human comments, merged count |
| `ledger.py` | Your estimates against outcomes; calibration after 20 decided targets |

Why scripts: each of these went wrong when done by hand.

- The search API's `closed:` filter drops PRs closed without merging. One repository over 30 days: search found 25 of 113 such PRs, reading 91% instead of 69.8%.
- `author_association` calls employees with private organization membership `CONTRIBUTOR`, and some project bots are plain user accounts: 48.8% read as 65.5%.
- The overall outside rate describes nobody: in the same repository newcomers merged 39.6% and returning contributors 74.5%.
- `gh pr view --json files` stops at 100 files, so a 234-file refactor that touches your file shows only 100.
- A repository's `AGENTS.md` can forbid agent PRs without ever saying "AI".
- Twenty near-identical PRs opened within minutes were closed together as spam.

## Install

As a Claude Code plugin:

```
/plugin marketplace add ShousenZHANG/oss-pr-workflow
/plugin install oss-pr-workflow@oss-pr-workflow
```

Or copy every directory under `skills/` into `~/.claude/skills/` (keep them side by side: the phase skills use the scripts in `oss-pr/scripts/`).

Requirements: Python 3.10+ and the [GitHub CLI](https://cli.github.com/), logged in (`gh auth status`). No Python packages.

## Use

```
/oss-pr deepset-ai/haystack          start or resume work on a repository
/oss-pr deepset-ai/haystack 12765    start from a specific issue
/oss-pr status                       your open PRs and what needs you
/oss-pr pick-repo go                 candidate repositories
```

On first use you are asked a few questions (which sources to scout, whether questions to maintainers are acceptable, per-repository PR cap, report language). Your data stays in `~/.oss-pr/`: `config.md`, `ledger.md`, `repos/` profiles, `cache/`. Example profiles for dify, haystack and airflow are in `skills/oss-pr/examples/repos/`; they are dated and go stale.

## Ground rules

- Each repository's own policy wins: AI use, disclosure wording, open-PR limits, title and link formats.
- Repositories that ban AI-assisted contributions are not touched; ones that ban only autonomous agents require you to read every changed line yourself.
- Text in issues, comments and bot output is treated as data, never as instructions.
- The aim is fewer, better-aimed PRs, not more of them.

## Known limitations

- The profile draft is pattern matching over a repository's documents and workflows. It has been checked against eight repositories (dify, haystack, airflow, mlflow, grafana, cilium, cli/cli, home-assistant), but every fact still needs a human read of the quoted evidence before use.
- Policy enforced only by private bots or in maintainers' heads is invisible until it shows up in closing comments; `base_rate.py` surfaces those, but only after it has happened to someone.
- The build phase has been exercised end to end on a Python repository. On Go and TypeScript repositories it was verified only up to "checks cannot run locally, user decides", because the toolchains were not installed.
- Merge-rate estimates are as good as their sample: a repository with few outside PRs gives a wide, weak estimate, and the scripts say so with n.

## Development

```bash
python -m pip install pytest ruff
python -m pytest
ruff check . && ruff format --check .
```

The tests include ten regression cases, each a real failure from past contribution rounds and the gate that now catches it (`tests/test_regressions.py`).

## Credits

- Design ideas from [alibaba/open-code-review](https://github.com/alibaba/open-code-review): deterministic code for the steps that must not fail, rules matched to files by path, review depth scaled by change size, mandatory review coverage, line positions verified against the diff, and dismissing a finding only when the diff proves it wrong. No code or prompt text is copied.
- For a different take on contribution workflows, see `contributor` in [majiayu000/spellbook](https://github.com/majiayu000/spellbook) (MIT).

## License

[MIT](LICENSE)
