# oss-pr config

Edit the values after the colons. Lines starting with `- ` are read by the scripts.

- sources: issues, campaigns, self-found
- ask_maintainers: yes
- allow_test_only_prs: yes
- per_repo_open_cap: 3
- per_repo_min_interval_hours: 24
- pause_after_silent_days: 7
- report_language: English

Notes:

- sources: `issues` = existing issues; `campaigns` = unclaimed slices of umbrella issues;
  `self-found` = defects you find in recently merged code (filed as an issue first when the repo requires one).
- ask_maintainers: `no` means targets that need a maintainer to choose an approach are skipped, not asked about.
- The rule that every public action is shown to you before it happens is not configurable.
