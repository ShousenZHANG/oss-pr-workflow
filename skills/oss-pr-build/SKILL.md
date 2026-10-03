---
name: oss-pr-build
description: Use when an open source contribution target has passed recon and the user said go - implementing the change, writing tests, reproducing the repository's CI locally, and reviewing the change before anything is pushed or shown as a PR.
---

# Build and self-review (phase 4)

## Overview

Produces a change that matches how this repository's merged PRs look, with a test that demonstrably fails without the fix, every CI job reproduced locally, and an independent review whose findings are all resolved.

**A test that cannot fail proves nothing. A review that skipped a file reviewed nothing. Both are checked by scripts, not by assertion.**

Scripts are in `../oss-pr/scripts/` from this skill's directory; call them by absolute path from inside the clone.

## 1. Set up

```bash
gh repo fork owner/repo --clone=false            # once; the fork is the user's
git -C <clone> fetch upstream && git -C <clone> worktree add ../<repo>-<slug> -b <type>/<slug> upstream/main
```

- Work only in the new worktree, never in the user's main checkout. Remove it (`git worktree remove`) when the target is merged or abandoned.
- Sync the fork's `main` with upstream before opening a PR: a stale fork made one repository's triage bot see 1,583 changed files.
- Use the repository's toolchain from the profile (hatch, uv, prek, make). Pass commit messages through a file (`git commit -F msg.txt`); shells eat backticks, `$` and backslashes.
- **Baseline:** run the relevant tests on clean `upstream/main` first. Failures there (platform-specific MIME types, segfaults in native libraries, missing signals on Windows) are pre-existing; write them down so they are not blamed on, or hidden by, the change.

## 2. Implement

- Copy the merged recipe from recon: same file layout, naming, error style, test style, size. The profile's `merged_pr_*_p90` gives the size envelope.
- Smallest change that fixes the problem. No drive-by refactors, formatting or "improvements".
- Read the docstrings and comments around the code you change; they are local contracts. Update them only where your change alters the behaviour they describe.
- `python rules_for.py owner/repo --changed` prints the profile's path rules (and the `ocr` CLI's per-file-type rules when installed) for the files you touched. Follow each one.

## 3. Test first, and prove the test can fail

1. Write the test, run it, see it **fail** for the right reason on the unfixed code.
2. Apply the fix, see it pass.
3. Revert the fix (or delete the decorator / guard under test) and confirm the test fails again. Record the test name with `"fails_without_fix": true` in `review.json`.

Rules from past rejections:
- **No tautological tests.** An expected value computed by the same constant or call as the code under test always passes. Hard-code the expected value or derive it independently.
- **Exercise the real path.** A default GET request context hid a POST-only branch; a test passed while testing nothing.
- **Type-only changes never change assertion semantics.** If a typing change forces an assertion to change meaning, stop and rethink. Never weaken an assertion; global search-and-replace in tests silently retargets them.
- Test the degenerate inputs: empty, unchanged, self-referential, already-correct.

## 4. Attack your own fix

Proving the bug is not proving the patch. For every guard you loosen or condition you add:

- **What input newly passes that did not before?** Enumerate them.
- **What runs after the check?** Is the result compared, branched on, used for a permission or identity decision, or assumed unique (`.one()`, `[0]`, an unguarded unpack)? Trace every caller.
- **Is a uniqueness or equality assumption loosened?** Exact to normalized, single to range: two things that were distinct can now collide.
- **Is old behaviour preserved for every input that is not the bug?**

**Changed a signature or added an injecting decorator?** Find callers by identity, not spelling. List every file that mentions the symbol, tests included, then check each binding and each call, since they are often far apart:

```python
unwrap(api.post)(api, session)  # direct
handler = unwrap(api.post)
handler()  # two-step: invisible to an `unwrap(...)(` pattern
Api.post(instance, ...)  # unbound: skips the instance decorator
monkeypatch.setattr(Api, "post", ...)  # replaced outright
monkeypatch.setattr(App, "is_agent", x)  # string form: invisible to a `.is_agent` grep
```

Report a count: *N call sites, N conform, 0 remaining*. Before removing or renaming an attribute, search for both `.name` and `"name"`. Confirm the import path of same-named symbols before "fixing" a caller you never touched.

**Grep discipline:** anchor patterns (`\bdb\.session[.(]`, not `db.session`, which also matches `db.session_factory`) and read one match in full before trusting a count.

## 5. Reproduce CI locally

Run every job the profile lists under "Local checks that match CI", not only the documented lint target. Guards that CI runs separately (AST guards, import linters, response-contract linters, type checkers with test-specific configs, release-note linters) are where PRs turn red after a clean local lint. A guard that crashes locally can exit 0; only a real run proves it.

Then:

```bash
python diff_check.py owner/repo --base upstream/main --plan "src/a.py,tests/test_a.py,releasenotes/notes/*"
```

FAIL lines block: unplanned files, likely secrets, non-ASCII where the profile forbids it, commits without `Signed-off-by` in DCO repositories. Every WARN gets a fix or a written reason.

## 6. Independent review

Review depth follows the size of the change (thresholds from alibaba/open-code-review):

| Change | Review |
|---|---|
| No file with 50+ changed lines, and fewer than 100 changed lines across 2+ files | One fresh subagent, one round |
| Above either threshold | First a risk list (each item: `[high/medium/low] location - problem - which file to read to confirm`), then one fresh subagent per bundle of related files (source with its tests, at most 10 files), two rounds |
| User asks for maximum | Three rounds |

Give each reviewer the diff, the issue text, the profile, and these four lenses:
1. **Defects** in the changed code: logic, boundaries, error paths, concurrency, security. Use the language checklist in `../oss-pr/checklists/`.
2. **Fidelity to precedent**: does it look like the merged PRs here, and is every claim the PR body will make actually true of this diff?
3. **Freshness**: re-run `contention_map.py --check` for the changed files; rebase onto current `upstream/main` and re-run the tests.
4. **Odds**: does anything found change the recon estimate? Update the ledger if so.

Reviewers report in this shape; write it to `review.json` in the worktree (not committed):

```json
{
  "coverage": [{"path": "src/a.py", "status": "reviewed"},
               {"path": "docs/x.md", "status": "skipped", "reason": "docs only"}],
  "tests": [{"name": "test_empty_messages", "fails_without_fix": true}],
  "findings": [{"path": "src/a.py", "start_line": 40, "end_line": 42, "severity": "high",
                "category": "behavior-change", "evidence": "old code returned 400 here",
                "resolution": "fixed"}]
}
```

**Resolving findings** (precision is not the goal here; a missed defect costs a rejection, a false alarm costs minutes):
- Every finding ends `fixed` or `dismissed: <proof>`. A dismissal needs evidence from the diff or the code, not an argument.
- Findings about **behaviour change, compatibility, concurrency, security, or an unused parameter** can be dismissed only with a test: `dismissed: test: test_old_status_kept passes`.

```bash
python findings_check.py review.json --base upstream/main
```

It fails when a changed file is missing from coverage, a line number does not exist in the new file, a finding is unresolved, a protected category was dismissed without a test, or no test was recorded as failing without the fix.

## Exit check

- `diff_check.py` PASS and every WARN answered.
- `findings_check.py` PASS (coverage 100%, all findings resolved, a failing-without-fix test recorded).
- All CI-equivalent commands pass, or each failure is shown to be pre-existing on the baseline.
- For `human-in-loop` repositories: the user has been told they must read every changed line before submission.

## Common mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Only `make lint` run | Red CI from guards and type checks lint does not run | Every CI job from the profile |
| Test never seen failing | Tautological test; reviewers delete it or close the PR | Revert-the-fix check |
| Callers found by one spelling | Breakage in tests CI runs and you did not | Identity search, report the count |
| Working in the user's checkout | Their work mixed with yours; leaked worktrees | Dedicated worktree, removed afterwards |
| Review skipped the "small" files | The bug was in the config file nobody read | Coverage table, `findings_check.py` |
| Dismissing a behaviour-change finding by argument | Regression found by the maintainer | Write the test |
