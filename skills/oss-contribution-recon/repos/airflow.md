# apache/airflow

Checked: 2026-10-03.

Sources: [PR template](https://github.com/apache/airflow/blob/main/.github/PULL_REQUEST_TEMPLATE.md), [05_pull_requests.rst](https://github.com/apache/airflow/blob/main/contributing-docs/05_pull_requests.rst), [32_open_pull_request_limit.rst](https://github.com/apache/airflow/blob/main/contributing-docs/32_open_pull_request_limit.rst).

## Before opening a PR

- **At most 5 open PRs** for users without write access, enforced by GitHub. Drafts do not count yet but will.
- **Title.** Imperative mood, no conventional-commit prefix: `Fix clearing with ...`, not `fix: ...` or `Fixed ...`. Generic titles ("Fix bug") fail the quality bar.
- **Body.** A real description of what and why; template-only bodies fail.
- **Issue link.** `closes: #ISSUE` or `related: #ISSUE`.
- **Gen-AI disclosure.** Tick the template checkbox and uncomment the line: `Generated-by: <Tool Name> following [the guidelines](https://github.com/apache/airflow/blob/main/contributing-docs/05_pull_requests.rst#gen-ai-assisted-contributions)`.
- **Newsfragment** only for significant user-facing changes: `airflow-core/newsfragments/<pr_number>.significant.rst`, added after the PR number exists.
- Commit author name and email become public on merge.

## Quality gate

PRs failing the criteria (title, description, static checks, disclosure, coherent scope) are converted to draft by tooling with a comment. A contributor with more than 3 flagged PRs may have them closed instead. Suspicious changes close all of the author's PRs.

## Local checks that match CI

```bash
prek run --from-ref main
uv run pytest <test path> -k <expr>
```

Observed on WSL with Python 3.14 as the system default: `uv sync --python 3.12 --no-install-package pykerberos --no-install-package python-ldap` avoids two native build failures. The `generate-openapi-spec` hook needs the CI image; when it fails locally, run its script directly and confirm the spec is unchanged.

## Waiting and pinging

- The template states 72 hours as the minimum reaction time; ping once after that.
- Example: #73729 had no label and no review 7 days after opening, with CI green.
