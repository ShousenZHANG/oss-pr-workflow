"""Map which files (and which line ranges) every open PR in a repository touches.

Build once per repo, then check candidate files against the cached map.

    python contention_map.py owner/repo --build
    python contention_map.py owner/repo --check src/file.py:120,245 --check tests/test_file.py
    python contention_map.py owner/repo --expect-contended path/known/to/be/touched.py

`--check PATH:LINES` gives the base-branch lines you plan to change in that file
(comma-separated); a bare PATH only reports which PRs touch the file.

File lists come from the REST `pulls/<n>/files` endpoint with pagination.
`gh pr view --json files` stops at 100 files per PR, so a large refactor PR
that also touches your file can silently drop out of a map built that way.
Line ranges are the base-branch lines each PR actually changes (context lines
around a hunk are not counted). They are numbered on the base the PR branched
from, so before a line is compared they are moved onto the current base with
GitHub's compare API (how the base changed the file since that PR branched).
When that cannot be told, or the base rewrote those very lines, the answer is
UNKNOWN, not DISTINCT.

A map with failed PRs can reject a target but cannot clear one; --check says
so whenever coverage is incomplete. --expect-contended is a sanity check: name
a file you know an open PR touches. If the map does not contain it, the map is
broken (wrong cache path, failed fetch) and every COLD result from it is
meaningless; the script exits with status 2.

Your own open PRs are flagged: two of your PRs editing the same lines conflict
as soon as one of them merges, so they have to go one after another.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from _config import cache_dir
from _gh import (
    base_changed_ranges,
    check_api_budget,
    current_login,
    gh_api_pages,
    gh_json,
    hunk_counts,
    parse_iso,
    run_main,
    use_utf8_stdout,
)

CACHE_VERSION = 3  # 3: head_sha and base_ref per PR, for mapping lines onto the current base
FILES_ENDPOINT_CAP = 3000
COMPARE_FILES_CAP = 300  # the compare endpoint lists at most 300 files
WORKERS = 6
HARD_CONFLICT_LINES = 10
HOT_FILE_LINES = 30
STALE_AFTER_HOURS = 24


def cache_path(repo: str) -> Path:
    return cache_dir() / "contention" / f"{repo.replace('/', '__')}.json"


def fetch_pr_files(repo: str, number: int) -> dict | None:
    """{"files": path -> changed base ranges (None when GitHub sent no patch), "added": [new paths]}."""
    files: dict[str, list[list[int]] | None] = {}
    added: list[str] = []
    entries = 0  # file entries GitHub returned; renames add a second key but one entry
    try:
        for page in gh_api_pages(f"repos/{repo}/pulls/{number}/files", max_pages=30):
            for f in page:
                entries += 1
                patch = f.get("patch")
                ranges = [list(r) for r in base_changed_ranges(patch)] if patch else None
                files[f["filename"]] = ranges
                if f.get("status") == "added":
                    added.append(f["filename"])
                previous = f.get("previous_filename")
                if previous:
                    files[previous] = ranges
    except RuntimeError:
        return None
    # GitHub's files endpoint stops at 3000 files; a PR that large may touch files the map cannot see.
    return {"files": files, "added": added, "incomplete": entries >= FILES_ENDPOINT_CAP}


def build(repo: str) -> dict:
    prs = []
    for page in gh_api_pages(f"repos/{repo}/pulls?state=open&sort=updated&direction=desc"):
        prs.extend(page)
    print(f"{repo}: {len(prs)} open PRs, fetching file lists ...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda pr: fetch_pr_files(repo, pr["number"]), prs))

    entries, failed = {}, []
    for pr, result in zip(prs, results, strict=True):
        if result is None:
            failed.append(pr["number"])
            continue
        entries[str(pr["number"])] = {
            "title": pr.get("title", ""),
            "author": (pr.get("user") or {}).get("login", "ghost"),
            "updated_at": pr.get("updated_at", ""),
            "draft": pr.get("draft", False),
            "head_sha": (pr.get("head") or {}).get("sha", ""),
            "base_ref": (pr.get("base") or {}).get("ref", ""),
            **result,
        }
    data = {
        "version": CACHE_VERSION,
        "repo": repo,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "open_prs": len(prs),
        "failed": failed,
        "prs": entries,
    }
    path = cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    print(f"cached {len(entries)}/{len(prs)} PRs to {path}; failed: {failed or 'none'}", file=sys.stderr)
    return data


def distance(line: int, ranges: list[list[int]]) -> int:
    """Lines between `line` and the nearest range; 0 when inside one."""
    best = None
    for start, end in ranges:
        d = 0 if start <= line <= end else min(abs(line - start), abs(line - end))
        best = d if best is None else min(best, d)
    return best if best is not None else 10**9


def shift_line(number: int, hunks: list[tuple[int, int, int]]) -> int | None:
    """Where an old base line sits on the current base, given the base's own hunks since then
    as (old start, old length, new length); None when the base rewrote that line itself."""
    delta = 0
    for old_start, old_len, new_len in hunks:
        if old_len == 0:  # `-k,0`: inserted after old line k
            if number <= old_start:
                return number + delta
        elif number < old_start:
            return number + delta
        elif number < old_start + old_len:
            return None
        delta += new_len - old_len
    return number + delta


def shift_ranges(ranges: list[list[int]], base_patch: str) -> list[list[int]] | None:
    """Map an open PR's ranges (numbered on the base it branched from) onto the current base.

    `base_patch` is how the base branch changed the file since that PR's merge base.
    None when a range end falls on lines the base itself rewrote: no honest mapping exists.
    """
    hunks = sorted((old_start, old_len, new_len) for old_start, old_len, _new_start, new_len in hunk_counts(base_patch))
    shifted = []
    for start, end in ranges:
        new_start, new_end = shift_line(start, hunks), shift_line(end, hunks)
        if new_start is None or new_end is None:
            return None
        shifted.append([new_start, new_end])
    return shifted


def verdict(line: int, ranges: list[list[int]] | None) -> str:
    if ranges is None:
        return "UNKNOWN (no patch from GitHub; read the PR diff)"
    d = distance(line, ranges)
    if d <= HARD_CONFLICT_LINES:
        return f"HARD CONFLICT ({d} lines away)"
    if d <= HOT_FILE_LINES:
        return f"HOT FILE ({d} lines away; check whether the other PR is near merging)"
    return f"DISTINCT ({d} lines away)"


def touching(data: dict, path: str) -> list[tuple[str, dict]]:
    return sorted(((n, pr) for n, pr in data["prs"].items() if path in pr["files"]), key=lambda item: int(item[0]))


def is_own(pr: dict, me: str) -> bool:
    return bool(me) and pr["author"].lower() == me.lower()


def parse_check(spec: str) -> tuple[str, list[int]]:
    """`path/to/file.py:120,245` -> ("path/to/file.py", [120, 245]); a bare path has no lines."""
    path, _, tail = spec.replace("\\", "/").rpartition(":")
    if path and tail and all(part.strip().isdigit() for part in tail.split(",")):
        return path, [int(part) for part in tail.split(",")]
    return spec.replace("\\", "/"), []


def base_drift(repo: str, compare_cap: int = COMPARE_FILES_CAP):
    """drift(number, pr, path): how the base branch changed `path` since that PR's merge base.

    "" when it did not change, the patch when it did, None when that cannot be told
    (failed request, no patch, or the 300-file compare list is full and the file is not in it).
    One compare request per PR, cached for the run.
    """
    cache: dict[str, list | None] = {}

    def drift(number: str, pr: dict, path: str) -> str | None:
        if number not in cache:
            head, base = pr.get("head_sha"), pr.get("base_ref")
            data = gh_json(["api", f"repos/{repo}/compare/{head}...{base}"]) if head and base else None
            cache[number] = None if data is None else data.get("files") or []
        files = cache[number]
        if files is None:
            return None
        for f in files:
            if path in (f.get("filename"), f.get("previous_filename")):
                return f.get("patch")
        return None if len(files) >= compare_cap else ""

    return drift


def check(data: dict, path: str, lines: list[int], me: str = "", drift=None) -> None:
    """Print who touches `path` and, for each of your lines, how close the other PR's change is.

    The other PR's lines are numbered on the base it branched from; `drift` (see base_drift)
    maps them onto the current base first. Without that mapping a line count is not comparable.
    """
    hits = touching(data, path)
    if not hits:
        print(f"COLD   {path}  (no open PR touches it)")
    for n, pr in hits:
        ranges = pr["files"][path]
        draft = " draft" if pr.get("draft") else ""
        if path in pr.get("added", []):
            print(f"SHARED {path}  #{n}{draft} @{pr['author']} updated {pr['updated_at'][:10]}  NEW FILE in that PR")
            print(f"        {pr['title'][:90]}")
            print("        that PR creates this file: if it exists on your base, the other PR is stale; else wait")
            continue
        shown = "no patch" if ranges is None else ", ".join(f"{a}-{b}" for a, b in ranges)
        print(f"SHARED {path}  #{n}{draft} @{pr['author']} updated {pr['updated_at'][:10]}  base lines {shown}")
        print(f"        {pr['title'][:90]}")
        if is_own(pr, me):
            print("        YOUR OWN PR: open the next one after this merges, or rebase and re-check the exact lines")
        if lines and ranges is not None and drift is not None:
            patch = drift(n, pr, path)
            if patch is None:
                for line in lines:
                    print(f"        your line {line}: UNKNOWN (cannot tell how the base moved since #{n} branched)")
                continue
            if patch:
                ranges = shift_ranges(ranges, patch)
                if ranges is None:
                    for line in lines:
                        print(f"        your line {line}: UNKNOWN (the base rewrote #{n}'s lines; read both diffs)")
                    continue
                print(f"        on the current base: lines {', '.join(f'{a}-{b}' for a, b in ranges)}")
        for line in lines:
            print(f"        your line {line}: {verdict(line, ranges)}")


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--build", action="store_true", help="fetch every open PR's file list and cache it")
    parser.add_argument(
        "--check", action="append", default=[], metavar="PATH[:LINES]", help="repo-relative path; repeatable"
    )
    parser.add_argument("--expect-contended", metavar="PATH", help="a file known to be touched by an open PR")
    args = parser.parse_args()

    if args.build:
        check_api_budget()
        data = build(args.repo)
    else:
        try:
            data = json.loads(cache_path(args.repo).read_text(encoding="utf-8"))
        except FileNotFoundError:
            sys.exit(f"no cached map for {args.repo}; run with --build first")
        if data.get("version") != CACHE_VERSION:
            sys.exit("cached map is from an older version of this script; run with --build")

    age_hours = (datetime.now(timezone.utc) - parse_iso(data["built_at"])).total_seconds() / 3600
    covered = len(data["prs"])
    print(f"map of {data['repo']}: {covered}/{data['open_prs']} open PRs, built {age_hours:.1f}h ago")
    if age_hours > STALE_AFTER_HOURS:
        print(f"WARNING: map older than {STALE_AFTER_HOURS}h; rebuild before trusting a COLD result")
    if data["failed"]:
        print(
            f"WARNING: {len(data['failed'])} PRs failed to fetch {data['failed']}; "
            "a COLD result here does not clear a file"
        )

    huge = sorted(int(n) for n, pr in data["prs"].items() if pr.get("incomplete"))
    if huge:
        print(f"WARNING: PRs {huge} change 3000+ files (GitHub's limit); a COLD result may miss them")
    if args.expect_contended:
        if not touching(data, args.expect_contended.replace("\\", "/")):
            print(f"ERROR: sanity check failed, {args.expect_contended} is not in the map; do not trust COLD results")
            sys.exit(2)
        print(f"sanity check passed: {args.expect_contended} is in the map")

    me = current_login() if args.check else ""
    drift = base_drift(args.repo)
    for spec in args.check:
        path, lines = parse_check(spec)
        check(data, path, lines, me, drift)


if __name__ == "__main__":
    run_main(main)
