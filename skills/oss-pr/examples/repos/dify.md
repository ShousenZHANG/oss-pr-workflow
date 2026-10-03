# langgenius/dify

Example profile, checked 2026-10-03. Rules change; re-check before relying on it.

Sources: [PR template](https://github.com/langgenius/dify/blob/main/.github/pull_request_template.md), [CONTRIBUTING.md](https://github.com/langgenius/dify/blob/main/CONTRIBUTING.md), [AGENTS.md](https://github.com/langgenius/dify/blob/main/AGENTS.md), [api/AGENTS.md](https://github.com/langgenius/dify/blob/main/api/AGENTS.md), [style.yml](https://github.com/langgenius/dify/blob/main/.github/workflows/style.yml).

## Facts

- checked: 2026-10-03
- ai_policy: disclosure
- ai_policy_quote: "If this PR was created by an automated agent, add `From <Tool Name>` as the final line" (PR template)
- disclosure_regex: (?m)^From \S+
- disclosure_location: body
- link_style: Fixes #N for single issues; Refs #N under umbrella issues (#41007 under #36544)
- title_style: conventional (`refactor(api): ...`)
- open_pr_limit: none
- dco: no
- cla: no
- signed_commits: no
- ascii_only: no
- ai_review_replies: allowed
- release_note: none
- issue_required: yes (first-time contributors' PRs without a linked issue are closed)
- min_ping_hours: none
- merged_pr_files_p90: 4
- merged_pr_lines_p90: 320

## Path rules

| glob | rule |
|------|------|
| `api/controllers/**` | no new SQLAlchemy calls or `db.session` in controllers: use `controllers.common.session.with_session` (`write=False` for reads) or `core.db.session_factory`; CI guard `check_no_new_controller_sqlalchemy.py` |
| `api/**` | no new `getattr` patterns or session mocks (CI guards `check_no_new_getattr.py`, `check_no_new_session_mock.py`); Pydantic v2 for request/response models |
| `api/core/**` | migration-only area: do not add files or move implementations into it |
| `api/tests/**` | the session-mock guard also covers tests |

## Local checks that match CI

Run from the repository root:

```bash
make lint
make type-check
make test TARGET_TESTS=./api/tests/<path>
uv run --project api python scripts/check_no_new_getattr.py --base-rev origin/main
uv run --project api python scripts/check_no_new_controller_sqlalchemy.py --base-rev origin/main
uv run --project api python scripts/check_no_new_session_mock.py --base-rev origin/main
```

`make lint` does **not** run the three `check_no_new_*` guards; only CI (`style.yml`) does. Backend integration tests are CI-only.

## How PRs die here

| Cause | Example PR | Avoid by |
|-------|------------|----------|
| First-time contributor, no linked issue | #36786, #41455 | Link a real issue (a link to a PR does not count) |
| Duplicate under the author-first policy: the issue author's PR is kept | #41483 | `issue_prs.py` before starting; claim by comment |
| Merge conflict during a queue cleanup | #38488 | Rebase as soon as `mergeable` turns `CONFLICTING` |

Deduplication keys on the link keyword: PRs that say `Fixes #N` for the same issue are deduplicated, while several PRs that say `Part of #N` / `Refs #N` under one umbrella can all merge (5 concurrent PRs under #36659 merged).

## Notes

- Umbrella campaigns as of the check date: #37403 removes `db.session` from controllers (every merged PR removes it; a pure session-factory swap is off-scope there); #36544 is dependency injection, where session-factory swaps fit (#41007). Measure with `issue_prs.py langgenius/dify 37403 --diff-match '^-.*\bdb\.session\b'`.
- Campaign PRs are merged in batches, mostly by one maintainer; days without a merge are normal.
- The stale workflow acts only on labelled PRs.
