# apache/airflow

Example profile, checked 2026-10-03. Rules change; re-check before relying on it.

Sources: [PR template](https://github.com/apache/airflow/blob/main/.github/PULL_REQUEST_TEMPLATE.md), [05_pull_requests.rst](https://github.com/apache/airflow/blob/main/contributing-docs/05_pull_requests.rst), [32_open_pull_request_limit.rst](https://github.com/apache/airflow/blob/main/contributing-docs/32_open_pull_request_limit.rst).

## Facts

- checked: 2026-10-03
- ai_policy: disclosure
- ai_policy_quote: "Gen-AI disclosure: the description must include a disclosure" (05_pull_requests.rst)
- disclosure_regex: (?m)^Generated-by: \S+
- link_style: `closes: #N` or `related: #N`
- title_style: imperative, no conventional prefix (`Fix clearing with ...`, not `fix: ...`)
- open_pr_limit: 5
- dco: no
- cla: no
- signed_commits: no
- ascii_only: no
- ai_review_replies: allowed
- release_note: newsfragment `airflow-core/newsfragments/<pr>.significant.rst` for significant user-facing changes only
- issue_required: no
- min_ping_hours: 72
- merged_pr_files_p90: 13
- merged_pr_lines_p90: 652

## Path rules

| glob | rule |
|------|------|
| `airflow-core/src/airflow/api_fastapi/**` | regenerate the OpenAPI spec when routes or models change; the `generate-openapi-spec` hook needs the CI image, so run its script directly and confirm the spec diff |
| `providers/**` | group changes per provider; never one PR per module in a burst |

## Local checks that match CI

```bash
prek run --from-ref main
uv run pytest <test path> -k <expr>
```

Observed on WSL with Python 3.14 as the system default: `uv sync --python 3.12 --no-install-package pykerberos --no-install-package python-ldap` avoids two native build failures.

## How PRs die here

| Cause | Example PR | Avoid by |
|-------|------------|----------|
| Batch of near-identical PRs flagged "AI Spam" and closed together | #72949-#72976 | `pacing.py`; group changes; read the Gen-AI clause first |
| Quality gate: generic title, template-only body, failing static checks, missing disclosure | (converted to draft by tooling) | `pr_body_check.py`; `prek run --from-ref main` |
| Umbrella issue already closed | #72986 | `issue_prs.py` prints the issue state |

## Notes

- PRs failing the quality criteria are converted to draft with a comment; more than 3 flagged PRs may be closed instead. Suspicious changes close all of the author's PRs.
- Ping once after 72 hours. #73729 had no label and no review 7 days after opening, with CI green.
- Commit author name and email become public on merge.
