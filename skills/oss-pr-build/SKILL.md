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
gh repo fork owner/repo --clone=false            # once, when the user has no fork yet
git -C <clone> fetch upstream
git -C <clone> remote set-head upstream --auto                   # record the remote's default branch
git -C <clone> symbolic-ref --short refs/remotes/upstream/HEAD   # the default branch: main, master, develop...
git -C <clone> worktree add ../<repo>-<slug> -b <type>/<slug> upstream/<default-branch>
```

- The default branch differs between repositories (mlflow uses `master`). The scripts detect it; write `upstream/<default-branch>` wherever this skill shows a base.
- Work only in the new worktree, never in the user's main checkout. Remove it (`git worktree remove`) when the target is merged or abandoned.
- Sync the fork's default branch with upstream before opening a PR: a stale fork made one repository's triage bot see 1,583 changed files.
- On Windows, large repositories need `git config core.longpaths true` in the clone (set it repo-locally) before checkout, and a short worktree path (for example `C:\w\<slug>`): Python and some test runners still fail on paths over 260 characters.
- Use the repository's toolchain from the profile (hatch, uv, prek, make). Pass commit messages through a file (`git commit -F msg.txt`); shells eat backticks, `$` and backslashes.
- **Baseline:** run the relevant tests on the clean default branch first, and record the toolchain versions. Failures there (platform-specific MIME types, segfaults in native libraries, missing signals on Windows, a lock file your tool version cannot parse) are pre-existing; write them down so they are not blamed on, or hidden by, the change.

### When a check cannot run locally

A required tool may be missing (Go, yarn, a CI-only container) or the platform may not support a check. Do not install system software without the user's consent, and do not pretend the check passed:
1. Run everything that can run, including the new test through the narrowest runner that works.
2. List each check that could not run, with the reason, in `review.json` under `"not_run"` and in the briefing to the user.
3. Ask the user to choose: install the tool, or accept that CI will be the first run of those checks (then watch CI closely after opening). Without that choice the exit check below is not met.

## 2. Implement

**Advise-only mode** when the profile says `code_by: user` (the repository wants contributors to write the code themselves): do not write the patch. Explain the defect, point to the lines, describe the change and the test in words, then review what the user writes, run every check on it, and do the rest of this phase (tests, CI reproduction, review) on their code. The user's own patch is what gets submitted.


- Copy the merged recipe from recon: same file layout, naming, error style, test style, size. The profile's `merged_pr_*_p90` gives the size envelope.
- Smallest change that fixes the problem. No drive-by refactors, formatting or "improvements".
- Read the docstrings and comments around the code you change; they are local contracts. Update them only where your change alters the behaviour they describe.
- `python rules_for.py owner/repo --changed` prints the profile's path rules (and the `ocr` CLI's per-file-type rules when installed) for the files you touched. Follow each one.

## 3. Test first, and prove the test can fail

1. Write the test, run it, see it **fail** for the right reason on the unfixed code.
2. Apply the fix, see it pass.
3. Revert the fix (or delete the decorator / guard under test) and confirm the test fails again.
4. Save both runs' output to files (`runs/without_fix.txt`, `runs/with_fix.txt`) and record them in `review.json`: `"fails_without_fix": true`, the `command`, `without_fix_log`, `with_fix_log`. `findings_check.py` reads the logs; a bare `true` is not accepted.

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
python diff_check.py owner/repo --plan "src/a.py,tests/test_a.py,releasenotes/notes/*"
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
3. **Freshness**: re-run `contention_map.py --check` for the changed files; rebase onto the current default branch and re-run the tests.
4. **Odds**: does anything found change the recon estimate? Update the ledger if so.

**When the host cannot spawn subagents** (some hosts only allow them on the user's explicit request): do the review yourself as a separate pass after the code is final, reading the diff top to bottom with the four lenses and the checklist, and record `"reviewer": "inline"` in `review.json` so the user knows the review was not independent.

Reviewers report in this shape; write it to `review.json` in the worktree (not committed):

```json
{
  "reviewer": "subagent",
  "coverage": [{"path": "src/a.py", "status": "reviewed"},
               {"path": "docs/x.md", "status": "skipped", "reason": "docs only"}],
  "tests": [{"name": "test_empty_messages", "fails_without_fix": true,
             "command": "pytest tests/test_a.py -k empty_messages",
             "without_fix_log": "runs/without_fix.txt", "with_fix_log": "runs/with_fix.txt"}],
  "not_run": [{"check": "yarn typecheck", "reason": "yarn not installed",
               "user_choice": "accept CI as the first run"}],
  "findings": [{"path": "src/a.py", "start_line": 40, "end_line": 42, "severity": "high",
                "category": "behavior-change", "evidence": "old code returned 400 here",
                "resolution": "fixed"}]
}
```

Severity: `critical`, `high`, `medium`, `low`. Category: `bug`, `security`, `performance`, `maintainability`, `test`, `style`, `documentation`, `behavior-change`, `compatibility`, `concurrency`, `unused-parameter`, `other`.

**Code no local test can reach** (only an end-to-end run exercises it, or the toolchain is missing and the user declined to install it): the test requirement can be waived only with the user's approval and a merged precedent that shipped the same kind of change without a unit test. Record it, and say so in the PR:

```json
"test_waiver": {"precedent": "#41600 added the same guard without a unit test",
                "ci_coverage": "connectivity-test e2e job exercises pod-to-pod-encryption",
                "approved_by_user": true}
```

**Resolving findings** (precision is not the goal here; a missed defect costs a rejection, a false alarm costs minutes):
- Every finding ends `fixed` or `dismissed: <proof>`. A dismissal needs evidence from the diff or the code, not an argument.
- Findings about **behaviour change, compatibility, concurrency, security, or an unused parameter** can be dismissed only with a test: `dismissed: test: test_old_status_kept passes`.

```bash
python findings_check.py review.json
```

It fails when a changed file is missing from coverage, a line number does not exist in the new file, a finding is unresolved, a protected category was dismissed without a test, or no test is proven by its saved output to fail without the fix and pass with it.

## Exit check

- `diff_check.py` PASS and every WARN answered.
- `findings_check.py` PASS (coverage 100%, all findings resolved, a test proven by saved runs to fail without the fix).
- All CI-equivalent commands pass, or each failure is shown to be pre-existing on the baseline, or each check that could not run is listed under `not_run` and the user chose how to handle it.
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
