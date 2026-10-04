# owner/repo

Profile layout. The `## Facts` block is machine-read: keep `- key: value` lines and the keys below.

## Facts

- checked: YYYY-MM-DD (the day the user checked it; `draft` until then)
- ai_policy: none | disclosure | human-in-loop | issue-restricted | banned-for-newcomers | banned
- ai_policy_quote: "the sentence that decides the level" (source file)
- disclosure_regex: regex the PR description must match, e.g. (?m)^From \S+
- disclosure_location: body | commit-trailer | body and commit-trailer
- pr_text_by: agent draft, user approves | user (the repo forbids AI-written PR text)
- code_by: agent, user reviews every line | user (the repo wants contributors to write the code)
- link_style: e.g. Fixes #N for single issues; Refs #N under umbrella issues
- title_style: conventional | imperative | free-form
- open_pr_limit: number or none
- dco: yes | no
- cla: yes | no
- signed_commits: yes | no
- ascii_only: no | yes | comma-separated globs where only ASCII is allowed
- ai_review_replies: allowed | own-words-only
- release_note: none | how to add one
- issue_required: yes | no (and label conditions)
- internal_labels: labels marking issues outsiders should not take, or none
- stale_close: stale-bot timers for PRs, or none
- min_ping_hours: hours before a polite ping, or none
- merged_pr_files_p90: 90th percentile of files changed by merged outside PRs
- merged_pr_lines_p90: 90th percentile of lines changed by merged outside PRs

## Path rules

| glob | rule |
|------|------|
| `dir/**` | rule that applies to files under dir |

## Local checks that match CI

```bash
# commands, and which ones the local lint target does not run
```

## How PRs die here

| Cause | Example PR | Avoid by |
|-------|------------|----------|

## Notes
