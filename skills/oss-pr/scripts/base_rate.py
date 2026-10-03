"""Measure how often outside contributors' PRs get merged in a repository.

    python base_rate.py owner/repo [--days 30] [--show-closed 10] [--no-split] [--me LOGIN]

Uses the REST pull list, not the search API. The search `closed:` date filter
misses many PRs closed without merging while still finding the merged ones,
which inflates the rate: on deepset-ai/haystack over 30 days it found all 261
merged PRs but only 25 of 113 closed-unmerged ones.

"External" excludes bots and anyone who merged a PR in the window. The second
rule matters because author_association only sees public organization
membership: employees with private membership show up as CONTRIBUTOR.

External PRs are split into newcomers and returning contributors: returning
means the author had a PR merged in this repo during the 12 months before the
PR was opened. The split usually matters more than the overall rate.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import timedelta

from _gh import (
    EXTERNAL_ASSOCIATIONS,
    check_api_budget,
    current_login,
    days_ago,
    gh_api_pages,
    gh_graphql,
    gh_json,
    is_bot,
    parse_iso,
    use_utf8_stdout,
)

LOOKBACK_DAYS = 365
AUTHORS_PER_QUERY = 10


@dataclass(frozen=True)
class ClosedPR:
    number: int
    author: str
    association: str
    user_type: str
    merged: bool
    closed_at: str
    title: str
    created_at: str = ""


def collect_closed(repo: str, days: float) -> list[ClosedPR]:
    """Closed PRs whose closed_at falls inside the window, newest activity first.

    Sorting by `updated` lets the walk stop early: a PR's updated_at is never
    earlier than its closed_at, so once updated_at leaves the window no later
    page can hold a PR closed inside it.
    """
    cutoff = days_ago(days)
    rows: list[ClosedPR] = []
    for page in gh_api_pages(f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc"):
        for pr in page:
            if parse_iso(pr["updated_at"]) < cutoff:
                return rows
            if not pr.get("closed_at") or parse_iso(pr["closed_at"]) < cutoff:
                continue
            user = pr.get("user") or {}
            rows.append(
                ClosedPR(
                    number=pr["number"],
                    author=user.get("login", "ghost"),
                    association=pr.get("author_association", ""),
                    user_type=user.get("type", ""),
                    merged=pr.get("merged_at") is not None,
                    closed_at=pr["closed_at"],
                    title=pr.get("title", ""),
                    created_at=pr.get("created_at", ""),
                )
            )
    return rows


MERGERS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: 100, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { updatedAt mergedBy { login } }
    }
  }
}
"""


def mergers(repo: str, days: float) -> set[str]:
    """Logins that merged at least one PR updated inside the window: people with write access."""
    owner, name = repo.split("/", 1)
    cutoff = days_ago(days)
    found: set[str] = set()
    cursor = None
    while True:
        variables = {"owner": owner, "name": name}
        if cursor:
            variables["cursor"] = cursor
        data = gh_graphql(MERGERS_QUERY, **variables)
        if data is None:
            raise RuntimeError("failed to list mergers")
        prs = data["data"]["repository"]["pullRequests"]
        for node in prs["nodes"]:
            if parse_iso(node["updatedAt"]) < cutoff:
                return found
            if node.get("mergedBy"):
                found.add(node["mergedBy"]["login"])
        if not prs["pageInfo"]["hasNextPage"]:
            return found
        cursor = prs["pageInfo"]["endCursor"]


def external_only(rows: list[ClosedPR], maintainers: set[str] = frozenset()) -> list[ClosedPR]:
    return [
        r
        for r in rows
        if r.association in EXTERNAL_ASSOCIATIONS and not is_bot(r.author, r.user_type) and r.author not in maintainers
    ]


def merge_history(repo: str, logins: list[str], since: str) -> dict[str, list[str] | None]:
    """mergedAt timestamps of each author's merged PRs in the repo since `since` (YYYY-MM-DD).

    Batches several authors into one GraphQL query with aliased searches. The
    `merged:` qualifier is reliable for merged PRs; only `closed:` drops PRs.
    None marks an author whose history could not be fetched.
    """
    history: dict[str, list[str] | None] = {}
    for start in range(0, len(logins), AUTHORS_PER_QUERY):
        batch = logins[start : start + AUTHORS_PER_QUERY]
        parts = [
            f'a{i}: search(query: "repo:{repo} is:pr is:merged author:{login} merged:>={since}", '
            f"type: ISSUE, first: 100) {{ nodes {{ ... on PullRequest {{ mergedAt }} }} }}"
            for i, login in enumerate(batch)
        ]
        data = gh_graphql("query { " + " ".join(parts) + " }")
        for i, login in enumerate(batch):
            result = (data or {}).get("data", {}).get(f"a{i}") if data else None
            history[login] = None if result is None else [n["mergedAt"] for n in result["nodes"] if n.get("mergedAt")]
    return history


def is_returning(created_at: str, merged_dates: list[str]) -> bool:
    """True when some merge happened in the LOOKBACK_DAYS before the PR was opened."""
    created = parse_iso(created_at)
    earliest = created - timedelta(days=LOOKBACK_DAYS)
    return any(earliest <= parse_iso(d) < created for d in merged_dates)


def split_by_status(
    rows: list[ClosedPR], history: dict[str, list[str] | None]
) -> tuple[list[ClosedPR], list[ClosedPR], list[ClosedPR]]:
    newcomers, returning, unknown = [], [], []
    for r in rows:
        dates = history.get(r.author)
        if dates is None:
            unknown.append(r)
        elif is_returning(r.created_at, dates):
            returning.append(r)
        else:
            newcomers.append(r)
    return newcomers, returning, unknown


def rate_line(label: str, rows: list[ClosedPR]) -> str:
    merged = sum(1 for r in rows if r.merged)
    closed = len(rows) - merged
    pct = f"{100 * merged / len(rows):.1f}%" if rows else "n/a"
    return f"{label:<28} merged {merged:>4}  closed-unmerged {closed:>4}  rate {pct}"


def last_comment(repo: str, number: int) -> str:
    comments = gh_json(["api", f"repos/{repo}/issues/{number}/comments?per_page=100"]) or []
    if not comments:
        return "(no comment)"
    last = comments[-1]
    body = " ".join((last.get("body") or "").split())
    return f"[{last['user']['login']}] {body[:220]}"


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--days", type=float, default=30, help="window by closed date (default 30)")
    parser.add_argument(
        "--show-closed",
        type=int,
        default=10,
        metavar="N",
        help="print the last comment of the N most recent closed-unmerged external PRs, to classify why they died",
    )
    parser.add_argument("--no-split", action="store_true", help="skip the newcomer / returning split (fewer API calls)")
    parser.add_argument("--me", help="login whose own status to report (default: the authenticated user)")
    args = parser.parse_args()
    check_api_budget()

    rows = collect_closed(args.repo, args.days)
    maintainers = mergers(args.repo, args.days)
    external = external_only(rows, maintainers)
    hidden = sorted({r.author for r in rows if r.association in EXTERNAL_ASSOCIATIONS and r.author in maintainers})

    print(f"{args.repo}: PRs closed in the last {args.days:g} days (REST pull list)")
    print(rate_line("all authors", rows))
    print(rate_line("external, non-bot", external))
    if hidden:
        print(f"excluded as maintainers despite CONTRIBUTOR/NONE association (they merged PRs): {', '.join(hidden)}")

    me = args.me or current_login()
    since = (days_ago(args.days + LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    if not args.no_split:
        authors = sorted({r.author for r in external})
        print(f"classifying {len(authors)} external authors by merge history ...", file=sys.stderr)
        history = merge_history(args.repo, authors, since)
        newcomers, returning, unknown = split_by_status(external, history)
        print(rate_line("  newcomers", newcomers))
        print(rate_line("  returning contributors", returning))
        if unknown:
            print(f"  {len(unknown)} PRs from authors whose history could not be fetched are in neither line")
    if me:
        mine = merge_history(args.repo, [me], days_ago(LOOKBACK_DAYS).strftime("%Y-%m-%d")).get(me)
        if mine is None:
            print(f"your status (@{me}): unknown (history fetch failed)")
        else:
            status = "RETURNING" if mine else "NEWCOMER"
            print(f"your status (@{me}): {status} ({len(mine)} merged PRs here in the last 12 months)")
    print("note: repos that land PRs outside GitHub's merge button show them as closed-unmerged;")
    print("      read the death causes below before trusting any rate.")

    dead = [r for r in external if not r.merged][: args.show_closed]
    if dead:
        print(f"\nlast comment on the {len(dead)} most recent closed-unmerged external PRs:")
        for r in dead:
            print(f"  #{r.number} @{r.author} ({r.association}) {r.title[:70]}")
            print(f"      {last_comment(args.repo, r.number)}")


if __name__ == "__main__":
    main()
