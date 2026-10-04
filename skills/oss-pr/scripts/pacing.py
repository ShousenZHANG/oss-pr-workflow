"""Decide whether you may open another PR in a repository right now.

    python pacing.py owner/repo [--author LOGIN]

Checks, in order (any STOP blocks a new PR; a failed lookup is a STOP, never a GO):
  - the profile's AI-policy gate: banned, or banned-for-newcomers when you have no merged PR here
  - FREEZE: one of your PRs here carries a spam / quality-violation / suspicious label
  - the repo's own open-PR limit (profile: open_pr_limit)
  - your per-repo cap (config: per_repo_open_cap, default 3)
  - the profile is your own, checked within 30 days (not a draft, not a bundled example)
  - all your open PRs here older than N days have had no response from anyone else
    (config: pause_after_silent_days, default 7): work elsewhere until someone answers;
    replies that cannot be read are a STOP
  - the last PR you opened here is more recent than the minimum interval
    (config: per_repo_min_interval_hours, default 24)
Exit status 0 means GO, 1 means STOP.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from _config import config_int, load_config, load_profile, profile_status
from _gh import current_login, gh_json, is_bot, parse_iso, run_main, use_utf8_stdout
from base_rate import merge_history

SEARCH_LIMIT = 100
FLAG_WORDS = ("spam", "quality violation", "suspicious", "invalid", "ai slop", "low quality")


@dataclass(frozen=True)
class MyPR:
    number: int
    state: str
    created_at: datetime
    labels: tuple[str, ...]
    responded: bool | None  # None when not checked (closed PRs, or young open ones)


@dataclass(frozen=True)
class Limits:
    repo_limit: int | None
    per_repo_cap: int
    min_interval: timedelta
    silent_after: timedelta


def decide(prs: list[MyPR], limits: Limits, now: datetime) -> list[tuple[str, str]]:
    """List of (STOP|OK, reason). Pure, so the rules are testable without GitHub."""
    out: list[tuple[str, str]] = []
    flagged = [p for p in prs if any(word in label.lower() for label in p.labels for word in FLAG_WORDS)]
    if flagged:
        earliest = min(flagged, key=lambda p: p.created_at)
        latest_flag = max(p.created_at for p in flagged)
        recovered = [p for p in prs if p.state == "MERGED" and p.created_at > latest_flag]
        if recovered:
            out.append(
                (
                    "OK",
                    f"{len(flagged)} PRs were flagged (earliest #{earliest.number}); recovered since: "
                    + ", ".join(f"#{p.number}" for p in recovered)
                    + " merged later. Keep the pace slow.",
                )
            )
        else:
            out.append(
                (
                    "STOP",
                    f"FREEZE: {len(flagged)} PRs flagged (earliest #{earliest.number}) and nothing merged since; "
                    "post one apology on the earliest and wait for a maintainer",
                )
            )
    open_prs = [p for p in prs if p.state == "OPEN"]
    if limits.repo_limit is not None and len(open_prs) >= limits.repo_limit:
        out.append(("STOP", f"repo limit: {len(open_prs)} open, the repo allows {limits.repo_limit}"))
    if len(open_prs) >= limits.per_repo_cap:
        out.append(("STOP", f"your cap: {len(open_prs)} open, per_repo_open_cap is {limits.per_repo_cap}"))
    old = [p for p in open_prs if now - p.created_at >= limits.silent_after]
    if open_prs and old and len(old) == len(open_prs) and all(p.responded is False for p in old):
        out.append(("STOP", f"all {len(old)} open PRs have had no response for {limits.silent_after.days}+ days"))
    unread = [p for p in old if p.responded is None]
    if unread:
        numbers = ", ".join(f"#{p.number}" for p in unread)
        out.append(
            ("STOP", f"could not read the replies on {numbers}; the silence rule cannot be checked (fails closed)")
        )
    if prs:
        latest = max(p.created_at for p in prs)
        if now - latest < limits.min_interval:
            wait = limits.min_interval - (now - latest)
            out.append(
                ("STOP", f"last PR opened {latest:%Y-%m-%d %H:%M} UTC; wait {wait.total_seconds() / 3600:.1f}h more")
            )
    if not out:
        out.append(("OK", f"{len(open_prs)} open here; within all limits"))
    return out


def responded(repo: str, number: int, me: str) -> bool | None:
    data = gh_json(["pr", "view", str(number), "--repo", repo, "--json", "comments,reviews"])
    if data is None:
        return None
    people = [c.get("author") for c in data.get("comments", [])] + [r.get("author") for r in data.get("reviews", [])]
    return any(p and p.get("login") and p["login"] != me and not is_bot(p["login"]) for p in people)


def ai_gate(ai_policy: str, merged_last_year: int | None) -> tuple[str, str] | None:
    """The profile's AI-policy gate, enforced here too so a STOP from phase 1 cannot be skipped."""
    level = (ai_policy or "").split(" ")[0].strip()
    if level == "banned":
        return "STOP", "the repo's AI policy bans AI-assisted contributions (profile: banned)"
    if level == "banned-for-newcomers":
        if merged_last_year is None:
            return "STOP", "AI policy bans newcomers and your merge history could not be read; fails closed"
        if merged_last_year == 0:
            return "STOP", "AI policy bans AI-assisted PRs from newcomers, and you have no merged PR here in 12 months"
    return None


def my_prs(repo: str, me: str, silent_after: timedelta, now: datetime) -> list[MyPR]:
    """Your PRs in the repo. A failed search is an error, never "no PRs": that would wave a new PR through."""
    search = ["search", "prs", "--repo", repo, "--author", me, "--limit", str(SEARCH_LIMIT)]
    fields = ["--json", "number,state,createdAt,labels"]
    recent = gh_json([*search, "--sort", "created", "--order", "desc", *fields])
    # Open PRs get their own search: in a mixed-state list of 100 an old open PR can fall off the end.
    open_items = gh_json([*search, "--state", "open", *fields])
    if recent is None or open_items is None:
        raise RuntimeError(f"could not list your PRs in {repo}; pacing cannot be checked (fails closed)")
    if len(open_items) >= SEARCH_LIMIT:
        raise RuntimeError(f"your open-PR list in {repo} is truncated at {SEARCH_LIMIT}; pacing fails closed")
    items = list({item["number"]: item for item in [*recent, *open_items]}.values())
    out = []
    for item in items:
        created = parse_iso(item["createdAt"])
        state = item["state"].upper()
        check = state == "OPEN" and now - created >= silent_after
        out.append(
            MyPR(
                number=item["number"],
                state=state,
                created_at=created,
                labels=tuple(label["name"] for label in item.get("labels", [])),
                responded=responded(repo, item["number"], me) if check else None,
            )
        )
    return out


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--author", help="GitHub login (default: the authenticated user)")
    args = parser.parse_args()

    me = args.author or current_login()
    if not me:
        raise RuntimeError("could not determine your GitHub login (gh auth status); pacing fails closed")
    config = load_config()
    facts, _rules, path = load_profile(args.repo)
    raw_limit = facts.get("open_pr_limit", "").split(" ")[0]
    limits = Limits(
        repo_limit=int(raw_limit) if raw_limit.isdigit() else None,
        per_repo_cap=config_int(config, "per_repo_open_cap"),
        min_interval=timedelta(hours=config_int(config, "per_repo_min_interval_hours")),
        silent_after=timedelta(days=config_int(config, "pause_after_silent_days")),
    )
    now = datetime.now(timezone.utc)
    results = decide(my_prs(args.repo, me, limits.silent_after, now), limits, now)
    ai_policy = facts.get("ai_policy", "")
    merged = None
    if ai_policy.startswith("banned-for-newcomers"):
        history = merge_history(args.repo, [me], (now - timedelta(days=365)).strftime("%Y-%m-%d")).get(me)
        merged = None if history is None else len(history)
    gate = ai_gate(ai_policy, merged)
    if gate:
        results = [gate, *[r for r in results if r[0] == "STOP" or not gate]]
    problem = profile_status(facts, path)
    if problem:
        results = [("STOP", problem), *[r for r in results if r[0] == "STOP"]]
    for status, reason in results:
        print(f"{status}: {reason}")
    stop = any(status == "STOP" for status, _ in results)
    print("RESULT: " + ("STOP" if stop else "GO"))
    sys.exit(1 if stop else 0)


if __name__ == "__main__":
    run_main(main)
