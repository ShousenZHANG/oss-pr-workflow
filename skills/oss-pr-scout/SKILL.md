---
name: oss-pr-scout
description: Use when a GitHub repository has a checked contribution profile and the next step is finding something to work on - when the good-first-issue list looks picked over, when an umbrella refactor issue is being sliced by many people, or when the issue list yields nothing unclaimed and work must come from the code itself.
---

# Scouting targets (phase 2)

## Overview

Produces at most five candidate targets, each with the facts the user needs to choose. In busy repositories a fresh, actionable issue is often claimed within 30-50 minutes, and labelled newcomer issues within a day or two; scouting therefore looks in three places and checks each finding for an existing claim before listing it.

**List facts, not scores. The user picks.**

Scripts are in `../oss-pr/scripts/` from this skill's directory. Read the profile first (`~/.oss-pr/repos/<owner>__<repo>.md`) and the user's `config.md`: `sources` decides which channels below are used, `allow_test_only_prs` and `ask_maintainers` filter what may be offered, and an `issue-restricted` AI policy limits issues to the ones that meet its condition.

## Channel 1: existing issues

```bash
gh issue list -R owner/repo --state open --limit 200 --json number,title,labels,assignees,comments,updatedAt
```

For every plausible issue read the **full** comment thread (the decisive "I'll take this" is often in the middle). Drop it if:
- it is assigned, or someone claimed it in a comment and was not told no;
- `issue_prs.py owner/repo N` shows an open PR, or a closed PR rejected on the merits;
- the profile marks it handled internally (a "handled internally" label or notice);
- it needs a maintainer to choose between approaches and `ask_maintainers: no`;
- the AI policy is `issue-restricted` and the issue lacks the required label or acceptance criteria.

A result count equal to `--limit` means the list was cut off: raise the limit or page.

## Channel 2: umbrella issues (campaigns)

Large issues sliced by many contributors ("remove X from all controllers", "add type hints to Y") are where most outside merges come from in some repositories.

1. `issue_prs.py owner/repo N` - who is doing what, what merged, how the closed ones died, and the link keyword each used. **The issue must be open.**
2. Find the remaining work on `main` by searching for the pattern the campaign removes, with an anchored regex (`\bdb\.session[.(]`, not `db.session`, which also matches `db.session_factory`). Count sites and re-read one in full.
3. Subtract sites inside files that open PRs touch (`contention_map.py --check`).
4. Measure the campaign rate for *your kind* of change: `issue_prs.py owner/repo N --diff-match '<regex for the change>'`. A rate computed over merged PRs that made a different change does not apply.
5. Match the size of merged slices; one module per PR, never one PR per file in a burst.

## Channel 3: defects in recently merged code

Used when `sources` includes `self-found`. Review is thinnest in code merged during the last two weeks:

```bash
git log upstream/main --since="14 days ago" --name-only --pretty=format: -- <src-dir> | sort | uniq -c | sort -rn | head -25
```

Read the newest changes with the checklist for the language (`../oss-pr/checklists/<language>.md`). Strongest finds: an internal inconsistency (the same value handled two ways), a check dropped in a refactor, empty input indexed without a guard, a `None` path that reaches code assuming a value.

**Leaf-position screen.** Keep only defects whose fix stays local. Drop anything touching authentication, billing, migrations, behaviour coupled to a UI, public API contracts, or semantics with many callers: those need maintainer design decisions and die in review.

**Prove it before listing it.** Read the whole function and its docstring (a comment often says the "inconsistency" is deliberate), look for a test that pins the current behaviour, check the layer below (constraints, migrations), and search closed issues and PRs for the idea. If the repository requires an issue before a PR (`issue_required` in the profile), the target becomes "file an issue, then a PR", and the issue gets the same draft-show-wait treatment as a PR.

## Delta scans

On a repo scanned before, look only at what changed since: issues created or updated since the last scan, and commits merged since. Record the scan time in the ledger notes.

## Output: the candidate table

At most five rows, each a fact the user can check:

| # | Target | Kind | Files (est.) | Contention | Precedent | Notes |
|---|---|---|---|---|---|---|
| 1 | #1234 empty list crashes `run()` | bug fix | 2 | COLD | 3 similar fixes merged in 30d | needs release note |

- **Kind**: bug fix, campaign slice, docs, test-only (only if allowed), new issue + fix.
- **Contention**: COLD / SHARED / your own PR, from `contention_map.py`.
- **Precedent**: merged PRs of the same kind here, with n.

Then ask the user which one to take. Do not rank without a citation; if two are equal, say so.

## Common mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Trusting `good first issue` labels | Claimed weeks ago in a comment | Read the whole thread |
| Searching only open PRs | A closed PR already tried it and shows why it died | `issue_prs.py` covers every state |
| Declaring a repo exhausted after scanning bug issues | Missed the umbrella issues where most outside merges happen | Check all enabled channels |
| Self-found "bug" that a comment calls deliberate | Closed as not a bug | Read the function, docstring and tests first |
| Unanchored grep for campaign sites | Phantom work from substring matches | `\bname[.(]`, read one hit |
