---
description: Contribute to an open source repository end to end - profile, scout, recon, build, ship - or check the status of your open source PRs.
argument-hint: "<owner/repo> [issue-number] | status | pick-repo <language>"
---

Use the `oss-pr` skill to handle this request: $ARGUMENTS

- `<owner/repo>` or `<owner/repo> <issue-number>`: start or resume work on that repository, beginning with its profile.
- `status`: report the user's open PRs (`pr_status.py`), sync the ledger (`ledger.py sync`), and say what needs the user's action.
- `pick-repo <language>`: list candidate repositories with `pick_repo.py` and let the user choose.
- No arguments: ask which of the above the user wants.

Every action other people can see is drafted and shown to the user first; nothing is posted without explicit approval.
