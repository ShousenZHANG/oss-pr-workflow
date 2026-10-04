"""List every PR in the same repo that references an issue, in any state.

    python issue_prs.py owner/repo 1234
    python issue_prs.py owner/repo 1234 --diff-match '^-.*\\bdb\\.session\\b'

Reads the issue timeline's cross-reference events, which catch PRs that only
mention the issue in their body. Searching for the number misses some of those
and matches unrelated text.

--diff-match counts only PRs whose diff matches a regex (multiline). Use it on
umbrella issues: a campaign's merge rate says nothing about a PR that makes a
different kind of change than the ones that merged.

Each PR is tagged link=closing (Fixes/Closes/Resolves #N) or link=reference
(Refs, Part of, a bare #N). Some repos deduplicate only PRs that claim to close
the issue, so on umbrella issues the keyword decides whether parallel PRs survive.
The issue's own state is printed first: never start a slice of a closed umbrella.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from _gh import gh, gh_json, run_main, use_utf8_stdout

TIMELINE_JQ = (
    '.[] | select(.event == "cross-referenced") | select(.source.issue.pull_request != null)'
    " | select(.source.issue.repository.full_name == $repo) | .source.issue.number"
)


def referencing_prs(repo: str, issue: int) -> list[int]:
    out = gh(
        [
            "api",
            f"repos/{repo}/issues/{issue}/timeline",
            "--paginate",
            "--jq",
            TIMELINE_JQ.replace("$repo", f'"{repo}"'),
        ]
    )
    if out is None:
        raise SystemExit(f"could not read the timeline of {repo}#{issue}")
    return sorted({int(n) for n in out.split()})


def link_kind(body: str, issue: int) -> str:
    """`closing` when the PR body uses a GitHub closing keyword for this issue, else `reference`."""
    closing = re.compile(rf"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*:?\s*(?:[\w.-]+/[\w.-]+)?#{issue}\b")
    return "closing" if closing.search(body or "") else "reference"


def issue_warning(issue: dict) -> str | None:
    if issue.get("state") == "closed":
        reason = issue.get("state_reason") or "unspecified"
        return f"WARNING: the issue is CLOSED ({reason}); do not start new work under it"
    return None


def pr_state(pr: dict) -> str:
    if pr.get("merged_at"):
        return "MERGED"
    return pr["state"].upper()


def kind_matches(patches: str, pattern: re.Pattern[str]) -> bool:
    """Whether a PR's combined patches contain the kind of change the campaign is about."""
    return bool(pattern.search(patches))


def diff_matches(repo: str, number: int, pattern: re.Pattern[str]) -> bool | None:
    patches = gh(["api", f"repos/{repo}/pulls/{number}/files?per_page=100", "--paginate", "--jq", '.[] | .patch // ""'])
    if patches is None:
        return None
    return kind_matches(patches, pattern)


def last_comment(repo: str, number: int) -> str:
    comments = gh_json(["api", f"repos/{repo}/issues/{number}/comments?per_page=100"]) or []
    if not comments:
        return "(no comment)"
    body = " ".join((comments[-1].get("body") or "").split())
    return f"[{comments[-1]['user']['login']}] {body[:200]}"


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("issue", type=int)
    parser.add_argument("--diff-match", metavar="REGEX", help="only count PRs whose diff matches this multiline regex")
    parser.add_argument("--no-comments", action="store_true", help="skip fetching the last comment of closed PRs")
    args = parser.parse_args()

    pattern = re.compile(args.diff_match, re.MULTILINE) if args.diff_match else None
    issue = gh_json(["api", f"repos/{args.repo}/issues/{args.issue}"])
    if not issue:
        sys.exit(f"ERROR: {args.repo}#{args.issue} not found (or not readable); nothing to check")
    if issue.get("pull_request"):
        print(f"note: #{args.issue} is a pull request, not an issue")
    labels = ", ".join(label["name"] for label in issue.get("labels", []))
    print(f"{args.repo}#{args.issue} [{(issue.get('state') or '?').upper()}] {issue.get('title', '')[:90]}")
    if labels:
        print(f"  labels: {labels}")
    warning = issue_warning(issue)
    if warning:
        print(warning)
    counts: Counter[str] = Counter()
    closed_by_link: Counter[str] = Counter()
    for number in referencing_prs(args.repo, args.issue):
        pr = gh_json(["api", f"repos/{args.repo}/pulls/{number}"])
        if pr is None:
            continue
        state = pr_state(pr)
        kind = ""
        if pattern is not None:
            matched = diff_matches(args.repo, number, pattern)
            if matched is None:
                counts["UNKNOWN_KIND"] += 1
                print(f"  #{number} {state:7} @{pr['user']['login']} (excluded: diff could not be fetched)")
                continue
            if not matched:
                print(f"  #{number} {state:7} @{pr['user']['login']} (excluded: diff does not match)")
                continue
            kind = " kind=match"
        counts[state] += 1
        link = link_kind(pr.get("body") or "", args.issue)
        if state == "CLOSED":
            closed_by_link[link] += 1
        end = (pr.get("merged_at") or pr.get("closed_at") or "-")[:10]
        merger = f" by @{pr['merged_by']['login']}" if pr.get("merged_by") else ""
        print(
            f"  #{number} {state:7} @{pr['user']['login']} opened {pr['created_at'][:10]} ended {end}{merger}"
            f" link={link}{kind}"
        )
        print(f"      {pr['title'][:90]}")
        if state == "CLOSED" and not args.no_comments:
            print(f"      last comment: {last_comment(args.repo, number)}")

    merged, closed = counts["MERGED"], counts["CLOSED"]
    decided = merged + closed
    rate = f"{100 * merged / decided:.1f}%" if decided else "n/a"
    scope = " (matching --diff-match only)" if pattern else ""
    print(
        f"\n{args.repo}#{args.issue}{scope}: merged {merged}, closed-unmerged {closed}, "
        f"open {counts['OPEN']}; rate {rate}"
    )
    if closed_by_link:
        print(f"closed-unmerged by link keyword: {dict(closed_by_link)}")
    if warning:
        print(warning)
    if counts["UNKNOWN_KIND"]:
        print(f"WARNING: {counts['UNKNOWN_KIND']} PRs could not be classified; the rate excludes them")


if __name__ == "__main__":
    run_main(main)
