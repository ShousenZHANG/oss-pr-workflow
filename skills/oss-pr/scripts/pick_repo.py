"""List candidate repositories for a language with the facts that predict whether outside PRs land.

    python pick_repo.py python [--min-stars 10000] [--limit 8] [--days 14]

For each active, non-archived repo: outside-contributor merge rate (REST pull
list, maintainers and bots excluded), number of distinct outside authors whose
PRs merged, the share of all merges by the single busiest author, open PRs, and
the AI-policy level read from the repo's own documents.

Facts only, no score. Rules of thumb from past rounds: skip repos whose AI policy
is "banned", repos where one author lands most merges (outsiders rarely get in),
and repos where almost no outside PR merges.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from _gh import check_api_budget, days_ago, gh_json, parse_iso, use_utf8_stdout
from base_rate import collect_closed, external_only, mergers
from profile_draft import classify_ai_policy, fetch_docs


def repo_facts(repo: str, days: float) -> dict:
    rows = collect_closed(repo, days)
    maintainers = mergers(repo, days)
    external = external_only(rows, maintainers)
    merged_all = [r for r in rows if r.merged]
    top_author, top_count = Counter(r.author for r in merged_all).most_common(1)[0] if merged_all else ("-", 0)
    ext_merged = [r for r in external if r.merged]
    meta = gh_json(["api", f"repos/{repo}"]) or {}
    branch = meta.get("default_branch", "main")
    tree_data = gh_json(["api", f"repos/{repo}/git/trees/{branch}?recursive=1"]) or {}
    tree = [e["path"] for e in tree_data.get("tree", []) if e.get("type") == "blob"]
    docs, templates, _workflows = fetch_docs(repo, branch, tree)
    level, _evidence = classify_ai_policy({**docs, **templates})
    open_prs = (gh_json(["api", f"search/issues?q=repo:{repo}+is:pr+is:open&per_page=1"]) or {}).get("total_count", "?")
    return {
        "repo": repo,
        "stars": meta.get("stargazers_count", 0),
        "ext_rate": f"{100 * len(ext_merged) / len(external):.0f}%" if external else "n/a",
        "ext_n": len(external),
        "ext_authors": len({r.author for r in ext_merged}),
        "top_share": f"{100 * top_count / len(merged_all):.0f}% @{top_author}" if merged_all else "n/a",
        "open_prs": open_prs,
        "ai_policy": level,
    }


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("language")
    parser.add_argument("--min-stars", type=int, default=10000)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--days", type=float, default=14, help="window for merge facts (default 14)")
    parser.add_argument("--exclude", default="", help="comma-separated owner/repo to skip (already used)")
    args = parser.parse_args()
    check_api_budget()

    found = gh_json(
        [
            "search",
            "repos",
            "--language",
            args.language,
            "--stars",
            f">={args.min_stars}",
            "--archived=false",
            "--sort",
            "stars",
            "--limit",
            str(args.limit * 3),
            "--json",
            "fullName,pushedAt",
        ]
    )
    skip = {s.strip().lower() for s in args.exclude.split(",") if s.strip()}
    recent = [
        r["fullName"]
        for r in found or []
        if parse_iso(r["pushedAt"]) >= days_ago(30) and r["fullName"].lower() not in skip
    ][: args.limit]
    print(f"{len(recent)} active {args.language} repos with {args.min_stars}+ stars; facts over {args.days:g} days\n")
    print("| repo | stars | outside merge rate (n) | outside authors merged | busiest author | open PRs | AI policy |")
    print("|---|---|---|---|---|---|---|")
    for repo in recent:
        print(f"  measuring {repo} ...", file=sys.stderr)
        try:
            f = repo_facts(repo, args.days)
        except RuntimeError as error:
            print(f"| {repo} | - | failed: {error} | | | | |")
            continue
        print(
            f"| {f['repo']} | {f['stars']} | {f['ext_rate']} ({f['ext_n']}) | {f['ext_authors']} | "
            f"{f['top_share']} | {f['open_prs']} | {f['ai_policy']} |"
        )


if __name__ == "__main__":
    main()
