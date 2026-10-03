# deepset-ai/haystack

Example profile, checked 2026-10-03. Rules change; re-check before relying on it.

Sources: [PR template](https://github.com/deepset-ai/haystack/blob/main/.github/pull_request_template.md), [CONTRIBUTING.md](https://github.com/deepset-ai/haystack/blob/main/CONTRIBUTING.md), [AGENTS.md](https://github.com/deepset-ai/haystack/blob/main/AGENTS.md).

## Facts

- checked: 2026-10-03
- ai_policy: disclosure
- ai_policy_quote: "If your PR was fully AI-generated, add a short disclaimer in the PR description" (CONTRIBUTING.md)
- disclosure_regex: (?i)AI (assistant|assistance)
- disclosure_location: body
- link_style: `- fixes #N` (template)
- title_style: conventional (`fix:`, `feat:`, `!` for breaking)
- open_pr_limit: 1
- dco: no
- cla: yes
- signed_commits: no
- ascii_only: releasenotes/notes/**
- ai_review_replies: allowed
- release_note: `hatch run release-note <name>`, reStructuredText in releasenotes/notes/
- issue_required: no
- min_ping_hours: none
- merged_pr_files_p90: 13
- merged_pr_lines_p90: 589

## Path rules

| glob | rule |
|------|------|
| `releasenotes/notes/**` | reStructuredText, double backticks for code, ASCII only; check with `PYTHONUTF8=1 reno lint` on Windows |
| `haystack/components/**` | AGENTS.md API rules: keyword-only optional params after `*`, clients created in `warm_up()`, `T \| None`, `:raises ValueError:` docstrings |
| `test/**` | follow `test/AGENTS.md`; DocumentStore tests go through the mixins in `haystack/testing/document_store.py` |

## Local checks that match CI

Hatch manages environments; do not run `python` or `pip` directly.

```bash
hatch run test:unit <path or -k expr>
hatch run test:types
hatch run fmt
PYTHONUTF8=1 reno lint
```

## How PRs die here

| Cause | Example PR | Avoid by |
|-------|------------|----------|
| First-time contributor opened a second PR (bot `first-pr-limit`) | #13072, #13073 | One open PR at a time |
| Another PR already merged the same fix | #12727 (superseded by #12983) | `issue_prs.py` and a contention check before starting |
| Unsigned CLA | #12806 | Sign when the CLA bot asks |

## Notes

- The Vercel check fails on PRs from forks (deployment authorization) and does not block merging (#13078 merged with it red).
- #13078 (bug fix with a regression test and a release note) was approved and merged 2h21m after opening.
- Enable "Allow edits and access to secrets by maintainers".
