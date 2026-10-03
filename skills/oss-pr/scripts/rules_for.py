"""Print the repo rules that apply to the files you changed or plan to change.

    python rules_for.py owner/repo path/a.py path/b.ts ...
    python rules_for.py owner/repo --changed [--base REF]   # files changed on this branch, committed or not

Rules come from the `## Path rules` table of the repo profile (glob -> rule).
When the `ocr` CLI from alibaba/open-code-review is installed, its built-in
per-file-type review rules are printed too (`ocr delegate rule`, no LLM needed).
Files that share a rule are grouped so each rule is read once.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import defaultdict

from _config import glob_match, load_profile
from _gh import default_base, run_git_or_exit, use_utf8_stdout


def group_rules(paths: list[str], rules: list[tuple[str, str]]) -> tuple[dict[str, list[str]], list[str]]:
    """(rule -> files it applies to, files no rule matches)."""
    grouped: dict[str, list[str]] = defaultdict(list)
    unmatched = []
    for path in paths:
        hits = [rule for pattern, rule in rules if glob_match(pattern, path)]
        if not hits:
            unmatched.append(path)
        for rule in hits:
            grouped[rule].append(path)
    return dict(grouped), unmatched


def changed_files(base: str) -> list[str]:
    """Files changed since the branch left `base`: committed, staged, unstaged, and new untracked files."""
    merge_base = run_git_or_exit(["merge-base", base, "HEAD"]).strip()
    tracked = run_git_or_exit(["diff", "--name-only", merge_base])
    untracked = run_git_or_exit(["ls-files", "--others", "--exclude-standard"])
    return list(dict.fromkeys(line for line in (tracked + untracked).splitlines() if line.strip()))


def ocr_rules(paths: list[str]) -> str | None:
    if not shutil.which("ocr"):
        return None
    result = subprocess.run(
        ["ocr", "delegate", "rule", *paths], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout if result.returncode == 0 else None


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--changed", action="store_true", help="use the files changed on the current branch")
    parser.add_argument("--base", help="base ref for --changed (default: detected upstream default branch)")
    args = parser.parse_args()

    paths = changed_files(args.base or default_base()) if args.changed else [p.replace("\\", "/") for p in args.paths]
    if not paths:
        sys.exit("no paths given")
    _facts, rules, profile = load_profile(args.repo)
    if profile is None:
        print(f"no profile for {args.repo}; run profile_draft.py first")
    else:
        print(f"profile: {profile}")
    grouped, unmatched = group_rules(paths, rules)
    for rule, files in grouped.items():
        print(f"\nRULE: {rule}")
        for f in files:
            print(f"  - {f}")
    if unmatched:
        print("\nno path rule:")
        for f in unmatched:
            print(f"  - {f}")
    extra = ocr_rules(paths)
    if extra:
        print("\n--- ocr delegate rule (alibaba/open-code-review built-in rules) ---")
        print(extra.strip())


if __name__ == "__main__":
    main()
