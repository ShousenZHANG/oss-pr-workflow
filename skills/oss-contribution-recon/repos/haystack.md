# deepset-ai/haystack

Checked: 2026-10-03.

Sources: [PR template](https://github.com/deepset-ai/haystack/blob/main/.github/pull_request_template.md), [CONTRIBUTING.md](https://github.com/deepset-ai/haystack/blob/main/CONTRIBUTING.md), [AGENTS.md](https://github.com/deepset-ai/haystack/blob/main/AGENTS.md).

## Before opening a PR

- **One open PR at a time** for community contributors (AGENTS.md, CONTRIBUTING "Requirements for Pull Requests"). A bot closes extra PRs from first-time contributors until their first PR is approved. Check with `gh pr list -R deepset-ai/haystack --author @me` before opening another.
- **CLA** via cla-assistant; sign when prompted.
- **Title.** Conventional commit type (`fix:`, `feat:`, `refactor:`, `test:` ...), `!` for breaking changes.
- **Issue link.** Template: `- fixes #<issue>`.
- **AI disclosure.** Only if the PR was *fully* AI-generated: a short disclaimer in the description, e.g. "This PR was fully generated with an AI assistant. I have reviewed the changes and run the relevant tests."
- Enable "Allow edits and access to secrets by maintainers".

## Release notes

Every user-facing PR needs one, and CI fails without it:

```bash
hatch run release-note <short-description>   # then edit releasenotes/notes/<name>.yaml (reStructuredText)
PYTHONUTF8=1 reno lint                       # PYTHONUTF8=1 avoids a UnicodeDecodeError on Windows code pages
```

Tests-only, CI-only, or docstring-only PRs can get the `ignore-for-release-notes` label from a maintainer instead.

## Local checks that match CI

Hatch manages environments; do not run `python` or `pip` directly (AGENTS.md).

```bash
hatch run test:unit <path or -k expr>
hatch run test:types
hatch run fmt
```

## Code rules that reviewers enforce

AGENTS.md lists them (API design, docstrings, typing, imports). Highlights: keyword-only optional params appended after `*`; clients created in `warm_up()`, not `__init__`; `T | None` not `Optional[T]`; `:raises ValueError:` style in docstrings; keep diffs scoped with no unrelated refactors.

## Known CI noise

- The **Vercel** check fails on PRs from forks (deployment authorization). It does not block merging (#13078 merged with it red).

## Waiting and pinging

- No stated minimum. Example: #13078 (bug fix with a regression test and release note) was approved and merged 2h21m after opening.
