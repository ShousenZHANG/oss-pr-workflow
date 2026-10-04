"""Track your open PRs in repositories you do not own.

    python pr_status.py [--author LOGIN] [--since-days 7] [--include-own]

For each open PR: merge state, CI counts and failing check names, review
decision, human reviews, and human comments newer than --since-days. Ends with
the count of PRs merged in the last 12 months outside your own repositories.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta

from _gh import days_ago, gh_json, is_bot, parse_iso, run_main, truncation_warning, use_utf8_stdout

VIEW_FIELDS = (
    "title,state,isDraft,mergeable,reviewDecision,updatedAt,labels,assignees,reviews,comments,statusCheckRollup"
)
FAILED_CONCLUSIONS = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"})


def summarize_checks(rollup: list[dict]) -> tuple[Counter[str], list[str]]:
    counts: Counter[str] = Counter()
    failed: list[str] = []
    for check in rollup or []:
        status = (check.get("status") or "").upper()
        if status and status != "COMPLETED":
            counts["PENDING"] += 1
            continue
        conclusion = (check.get("conclusion") or check.get("state") or "UNKNOWN").upper()
        counts[conclusion] += 1
        if conclusion in FAILED_CONCLUSIONS:
            failed.append(check.get("name") or check.get("context") or "?")
    return counts, failed


def human(author: dict | None) -> bool:
    login = (author or {}).get("login", "")
    return bool(login) and not is_bot(login) and "copilot" not in login.lower()


def report(repo: str, number: int, since_iso: str) -> None:
    pr = gh_json(["pr", "view", str(number), "--repo", repo, "--json", VIEW_FIELDS])
    if pr is None:
        print(f"==== {repo}#{number}  FETCH FAILED")
        return
    counts, failed = summarize_checks(pr["statusCheckRollup"])
    draft = " DRAFT" if pr["isDraft"] else ""
    print(f"==== {repo}#{number}{draft} {pr['title'][:80]}")
    print(f"     mergeable={pr['mergeable']} review={pr['reviewDecision'] or '-'} updated={pr['updatedAt'][:16]}")
    print(f"     checks={dict(counts)} failing={failed or '-'}")
    labels = [label["name"] for label in pr["labels"]]
    assignees = [a["login"] for a in pr["assignees"]]
    if labels or assignees:
        print(f"     labels={labels} assignees={assignees}")
    for review in pr["reviews"]:
        if human(review.get("author")):
            body = " ".join((review.get("body") or "").split())
            print(
                f"     REVIEW @{review['author']['login']} {review['state']} {review['submittedAt'][:16]}: {body[:200]}"
            )
    for comment in pr["comments"]:
        if human(comment.get("author")) and comment["createdAt"] >= since_iso:
            body = " ".join((comment.get("body") or "").split())
            print(f"     COMMENT @{comment['author']['login']} {comment['createdAt'][:16]}: {body[:300]}")
    inline = gh_json(["api", f"repos/{repo}/pulls/{number}/comments?per_page=100"]) or []
    for c in inline:
        if human(c.get("user")) and c["created_at"] >= since_iso:
            body = " ".join((c.get("body") or "").split())
            print(f"     INLINE @{c['user']['login']} {c['created_at'][:16]} {c['path']}:{c.get('line')}: {body[:200]}")


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--author", help="GitHub login (default: the authenticated user)")
    parser.add_argument("--since-days", type=float, default=7, help="show human comments newer than this (default 7)")
    parser.add_argument("--include-own", action="store_true", help="also list PRs in repositories you own")
    args = parser.parse_args()

    login = args.author or (gh_json(["api", "user"]) or {}).get("login")
    if not login:
        raise SystemExit("could not determine the GitHub login; pass --author")
    since_iso = days_ago(args.since_days).strftime("%Y-%m-%dT%H:%M:%SZ")

    def foreign(item: dict) -> bool:
        return args.include_own or not item["repository"]["nameWithOwner"].lower().startswith(f"{login.lower()}/")

    open_prs = (
        gh_json(
            ["search", "prs", "--author", login, "--state", "open", "--limit", "100", "--json", "repository,number"]
        )
        or []
    )
    warning = truncation_warning(len(open_prs), 100, "the open-PR search")
    if warning:
        print(warning)
    open_prs = sorted(
        (p for p in open_prs if foreign(p)), key=lambda p: (p["repository"]["nameWithOwner"], p["number"])
    )
    print(f"@{login}: {len(open_prs)} open PRs; human comments shown since {since_iso[:10]}\n")
    for item in open_prs:
        report(item["repository"]["nameWithOwner"], item["number"], since_iso)

    year_ago = (days_ago(0) - timedelta(days=365)).strftime("%Y-%m-%d")
    merged = (
        gh_json(
            [
                "search",
                "prs",
                "--author",
                login,
                "--merged",
                "--merged-at",
                f">={year_ago}",
                "--limit",
                "1000",
                "--json",
                "repository,number,closedAt",
            ]
        )
        or []
    )
    warning = truncation_warning(len(merged), 1000, "the merged-PR search")
    if warning:
        print(warning)
    merged = [m for m in merged if foreign(m)]
    per_repo = Counter(m["repository"]["nameWithOwner"] for m in merged)
    recent = [m for m in merged if parse_iso(m["closedAt"]) >= days_ago(args.since_days)]
    print(f"\nmerged since {year_ago} outside your repositories: {len(merged)}  {dict(per_repo.most_common())}")
    for m in recent:
        print(f"  recently merged: {m['repository']['nameWithOwner']}#{m['number']} ({m['closedAt'][:10]})")


if __name__ == "__main__":
    run_main(main)
