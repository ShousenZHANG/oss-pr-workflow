# langgenius/dify

Checked: 2026-10-03.

Sources: [PR template](https://github.com/langgenius/dify/blob/main/.github/pull_request_template.md), [CONTRIBUTING.md](https://github.com/langgenius/dify/blob/main/CONTRIBUTING.md), [AGENTS.md](https://github.com/langgenius/dify/blob/main/AGENTS.md), [api/AGENTS.md](https://github.com/langgenius/dify/blob/main/api/AGENTS.md), [style.yml](https://github.com/langgenius/dify/blob/main/.github/workflows/style.yml).

## Before opening a PR

- **Linked issue is required.** First-time contributors' PRs without a linked *issue* are closed; a link to another PR does not count (closing comments on #36786, #41455).
- **Issue link syntax.** The template asks for `Fixes #<issue>`. On umbrella issues that must stay open, merged PRs use `Refs #<issue>` (for example #41007 under #36544).
- **AI disclosure.** Put `From <Tool Name>` (for example `From Claude Code`) as the final line of the description (template comment). CONTRIBUTING: you must understand and verify every change; repetitive low-quality submissions may be closed.
- **Duplicates.** One PR is kept per issue, and the issue author's PR has priority ("author-first policy", #41483). Check `issue_prs.py` before starting.
- **Title.** Conventional commits with scope, e.g. `refactor(api): ...`.

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

`make lint` does **not** run the three `check_no_new_*` guards; only CI (`style.yml`) does. Run them locally or CI fails on a PR that passed `make lint`. Backend integration tests are CI-only.

## Code rules that reviewers enforce

- `api/core/` is migration-only: do not add files or move implementations into it (`api/AGENTS.md`).
- Controllers get their database session from `controllers.common.session.with_session` (`@with_session(write=False)` for read-only handlers) or from `core.db.session_factory`; do not add new `db.session` use in controllers (guard script above).
- Pydantic v2 for request and response models.

## Umbrella campaigns (as of the check date)

- **#37403** — remove `db.session` from controllers. Merged PRs all remove `db.session`; a PR that only swaps `Session(db.engine)` for the session factory is off-scope there.
- **#36544** — dependency injection in current Flask code. Session-factory swaps fit here (#41007).
- Campaign PRs are merged in batches, mostly by one maintainer; days without a merge are normal.

Measure before quoting a rate: `issue_prs.py langgenius/dify 37403 --diff-match '^-.*\bdb\.session\b'`.

## How PRs die here (from closing comments)

| Cause | Example PR | Avoid by |
|-------|------------|----------|
| First-time contributor, no linked issue | #36786, #41455 | Link a real issue; open one first if needed |
| Duplicate of the issue author's PR | #41483 | Run `issue_prs.py` before starting; claim by comment |
| Merge conflict during queue cleanup | #38488 | Rebase as soon as `mergeable` turns `CONFLICTING` |

## Waiting and pinging

- No stated minimum response time. The stale workflow acts only on labelled PRs.
