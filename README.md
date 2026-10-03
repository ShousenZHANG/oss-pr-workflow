# oss-pr-recon

English | [简体中文](README.zh-CN.md)

A Claude Code skill and four small scripts for one question: **is this open source contribution target actually free, and will a PR on it survive?** Answer it before writing any code.

In busy repositories (10k+ stars, dozens of merges a week) most rejected PRs are not rejected for code quality. They die because someone else already claimed the issue, another open PR is rewriting the same function, the repo keeps only one PR per issue, or the PR quietly missed a disclosure or release-note rule. This skill checks each of those in order.

## What is inside

```
skills/oss-contribution-recon/
├── SKILL.md            the method: rules → issue → files → hunks → governance → odds
├── scripts/            the measurements, so they are not re-typed (and re-broken) each time
└── repos/              per-repository rule files, each dated and sourced
```

| Script | What it answers |
|--------|-----------------|
| `issue_prs.py owner/repo N` | Every PR that ever referenced issue N, in any state, with the last comment on each closed one. `--diff-match REGEX` counts only PRs that made a given kind of change. |
| `contention_map.py owner/repo --build` / `--check PATH --line N` | Which files and base-branch line ranges every open PR touches, and whether your planned edit is a hard conflict, a hot file, or distinct. |
| `base_rate.py owner/repo` | Merge rate of outside contributors' PRs over the last N days, and why the closed ones died. |
| `pr_status.py` | Your own open PRs in other people's repositories: CI, reviews, new comments, merged count over 12 months. |

Why scripts instead of prose: every one of these measurements went wrong at least once when written inline.

- The search API's `closed:` date filter drops many PRs closed without merging. On one repo over 30 days it found 261 merged and 25 closed (91%); the REST pull list shows 261 and 113 (69.8%).
- `author_association` marks employees with private org membership as `CONTRIBUTOR`, and some project bots are plain user accounts. On the same repo that turned a 48.8% outside-contributor rate into 65.5%.
- `gh pr view --json files` stops at 100 files, so a 234-file refactor PR that also touches your file shows up with only 100.
- A campaign's merge rate says nothing about your PR if the merged ones made a different kind of change.

## Install

As a Claude Code plugin:

```
/plugin marketplace add ShousenZHANG/oss-pr-recon
/plugin install oss-pr-recon@oss-pr-recon
```

Or copy `skills/oss-contribution-recon/` into `~/.claude/skills/`.

Requirements: Python 3.10+ and the [GitHub CLI](https://cli.github.com/) logged in (`gh auth status`). No Python packages.

## Use

Ask Claude to pick or vet a target ("is issue 1234 in owner/repo free to work on?") and the skill loads. The scripts also run on their own:

```bash
python skills/oss-contribution-recon/scripts/base_rate.py deepset-ai/haystack --days 30
python skills/oss-contribution-recon/scripts/contention_map.py deepset-ai/haystack --build
python skills/oss-contribution-recon/scripts/contention_map.py deepset-ai/haystack \
    --check haystack/components/generators/chat/openai.py --line 300
python skills/oss-contribution-recon/scripts/pr_status.py --since-days 3
```

## Ground rules

- Nothing here posts, comments, or opens PRs. It only reads.
- Each repository's own policy wins: AI-disclosure lines, open-PR limits, and title formats differ, and the files in `repos/` record what was true on the date they show. Re-check old ones.
- This is for making fewer, better-aimed PRs, not more of them.

## Development

```bash
python -m pytest
ruff check . && ruff format --check .
```

## Credits

- Design ideas from [alibaba/open-code-review](https://github.com/alibaba/open-code-review): keep steps that must not go wrong in deterministic code, and match rules to files by path. No code or prompt text is copied.
- For an end-to-end contribution workflow to pair with this skill, see `contributor` in [majiayu000/spellbook](https://github.com/majiayu000/spellbook) (MIT).

## License

[MIT](LICENSE)
