---
name: oss-pr-profile
description: Use when starting contribution work on a GitHub repository that has no checked profile in ~/.oss-pr/repos or whose profile is older than 30 days, or when a PR was closed for a rule nobody wrote down - before choosing any issue to work on.
---

# Repository profile (phase 1)

## Overview

A profile records the rules that decide whether a PR survives in one repository: AI policy, disclosure wording, open-PR limits, link syntax, release notes, the checks CI runs, and how outside PRs usually die. It is written once, checked by the user, and reused for every target in that repository.

**Read the rules before the code. One unread sentence ("first-time contributors must not use code agents") makes every later step worthless.**

Scripts are in the `oss-pr` skill next to this one: `../oss-pr/scripts/` from this skill's directory.

## Steps

1. **Draft.** `python profile_draft.py owner/repo`. It reads the PR template, CONTRIBUTING, root and `.github/` AGENTS.md / CLAUDE.md, AI-policy files, contributing guides (including `Documentation/contributing/`), documents those files link to under an AI / policy / contributing title (even in another repository, such as an organisation's community repo), every CI workflow and the scripts they call, and 15 recently merged PRs from outside contributors. It writes `~/.oss-pr/repos/<owner>__<repo>.md` (or `.draft.md` next to an existing profile). Every fact carries `(auto, verify)`.
2. **See how PRs actually die, before judging the policy.** `python base_rate.py owner/repo --days 30 --show-closed 20`. It prints who closed each recent outside PR and why, and counts the closures that mention AI use. Practice can be stricter than the written policy: in one repository at least 10 of 58 closed outside PRs in a month were closed because the patch was AI-written, while the written policy only asked for disclosure.
3. **Read the evidence, not just the facts.** Open each quoted line under "AI policy evidence" and "Other evidence" in its source file and read the surrounding paragraph. The detector is a pattern matcher: it misses rules phrased unusually and flags lines that only mention AI.
4. **Settle the AI policy level.** Pick exactly one, quote the sentence that decides it into the profile, and apply the gate:

   | Level | Example wording | Gate |
   |---|---|---|
   | `banned` | "we do not accept AI-generated contributions" | **Stop.** Tell the user; do not continue in this repo. |
   | `banned-for-newcomers` | "first-time contributors must not use code agents"; or maintainers close newcomers' AI-written patches in practice (step 2) | The user's status is printed by `base_rate.py`. NEWCOMER: stop. RETURNING: continue as `disclosure` or `human-in-loop`. |
   | `issue-restricted` | "only issues labelled `help wanted` with acceptance criteria" | Continue; write the condition into the profile; scouting may only offer issues that meet it. |
   | `human-in-loop` | "autonomous agents are not allowed; a human must review every change"; "write the code yourself, AI may assist" (also set `code_by: user`) | Continue; before submission the user must confirm they read (and, where required, wrote) every changed line, and the PR says so truthfully. |
   | `disclosure` | "state in the PR description if AI tools were used" | Continue; copy the exact required wording. |
   | `none` | no rule found | Continue; disclose briefly anyway. |

   When two readings are plausible, take the stricter one and ask the user. When step 2 shows AI-related closures that the written policy does not explain: if the closed authors were mostly newcomers, use `banned-for-newcomers`; otherwise move one row up the table. Tell the user which closures drove the decision.
5. **Read the bot rules.** The "Bot rules" section quotes workflow files and the scripts they run. Automation often decides more than the documents: closing PRs without a triaged issue, closing when template sections are missing, stale timers, "AI slop" detection, protected paths outsiders may not touch, CI that only starts after a member comments. Turn each real rule into a fact, a path rule, or a "How PRs die here" row.
6. **Fill in what the script cannot know.**
   - `disclosure_regex`: a regex that matches the required disclosure line, built from the template and the disclosure lines seen in merged PRs (for example `(?m)^From \S+`, `Generated-by:`, `AIL:\s*\d`). Write `none required` when the repository asks for nothing. The draft's `FILL IN` placeholder makes `pr_body_check.py` fail until this is done.
   - `disclosure_location`: `body`, `commit-trailer`, or both. Some repositories want a `Co-Authored-By` trailer instead of a PR-body line.
   - `pr_text_by`: `user` when the repository forbids AI-written PR descriptions or communication; then phase 5 hands the user facts and a skeleton instead of a finished text.
   - `code_by`: `user` when the repository wants contributors to write the code themselves ("AI may help you understand, but write the actual code yourself"); then phase 4 runs in advise-only mode.
   - `ai_review_replies`: `own-words-only` when the repo forbids AI-written answers to maintainers.
   - `link_style`: what merged PRs actually write. On umbrella issues that must stay open, a closing keyword (`Fixes #N`) can close the issue or get the PR deduplicated; check which keyword merged PRs under umbrellas used. Some repositories also want the reference in the commit message.
   - Conditions for `issue-restricted` repos, label requirements (e.g. an issue must carry `ready`), and `internal_labels` that mark issues outsiders should not take.
7. **Path rules.** Add a row per directory with its own conventions: scoped AGENTS.md files (the script lists them, vendored code excluded), guard scripts CI runs for a directory, generated-code directories. These rows are printed by `rules_for.py` for the files a change touches.
8. **Local checks.** From the CI commands the script listed, write the commands that reproduce every CI job locally. Mark the ones the documented local lint target does not run (in one repo three AST guard scripts ran only in CI and turned PRs red), and requirements such as "every commit must build on its own".
9. **How PRs die here.** From step 2, one table row per cause (bot rule, duplicate, merits, AI policy in practice, stale, conflict, CLA, DCO) with an example PR.
10. **Show the profile to the user** and let them correct it. Then remove `(auto, verify)` from the facts they confirmed and set `checked:` to today.

## Profile shape

Copy the layout of `../oss-pr/examples/repos/_template.md`. The `## Facts` block is read by the scripts (`- key: value` lines), so keep its keys:

`checked, ai_policy, ai_policy_quote, disclosure_regex, disclosure_location, pr_text_by, code_by, link_style, title_style, open_pr_limit, dco, cla, signed_commits, ascii_only, ai_review_replies, release_note, issue_required, internal_labels, stale_close, min_ping_hours, merged_pr_files_p90, merged_pr_lines_p90`

## Exit check

- Profile exists, `checked:` within 30 days, user has seen it.
- AI policy gate passed (not `banned`; not `banned-for-newcomers` for a newcomer).
- Personal preconditions listed for the user: CLA to sign, DCO sign-off, commit signing setup. These are not probabilities; an unsigned CLA makes the odds zero.

## Common mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Choosing a target before reading the AI policy | A whole batch of work in a repo that bans it | Profile first, always |
| Trusting `(auto)` facts | A missed rule closes the PR | Read every quoted line in context |
| Copying the template's link syntax | `Fixes #N` on an umbrella deduplicates or closes it | Use what merged PRs wrote |
| Reading only the root AGENTS.md | Directory rules (ASCII-only release notes, guard scripts) missed | Add path rules for scoped files |
| Profile from months ago | Open-PR limits and AI rules change (one repo went from 2 to 1 open PR) | Re-check after 30 days |
