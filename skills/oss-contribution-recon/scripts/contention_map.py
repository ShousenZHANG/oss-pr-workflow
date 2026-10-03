"""Map which files (and which line ranges) every open PR in a repository touches.

Build once per repo, then check candidate files against the cached map.

    python contention_map.py owner/repo --build
    python contention_map.py owner/repo --check path/to/file.py [--line 120 --line 240]

File lists come from the REST `pulls/<n>/files` endpoint with pagination.
`gh pr view --json files` stops at 100 files per PR, so a large refactor PR
that also touches your file can silently drop out of a map built that way.

A map with failed PRs can reject a target but cannot clear one; --check says
so whenever coverage is incomplete.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from _gh import base_side_ranges, gh_api_pages, parse_iso, use_utf8_stdout

CACHE_DIR = Path.home() / ".cache" / "oss-pr-recon"
WORKERS = 6
HARD_CONFLICT_LINES = 10
HOT_FILE_LINES = 30
STALE_AFTER_HOURS = 24


def cache_path(repo: str) -> Path:
    return CACHE_DIR / f"{repo.replace('/', '__')}.json"


def fetch_pr_files(repo: str, number: int) -> dict[str, list[list[int]] | None] | None:
    """Map path -> base-side hunk ranges for one PR.

    A None range list means GitHub sent no patch (binary or too large).
    """
    files: dict[str, list[list[int]] | None] = {}
    try:
        for page in gh_api_pages(f"repos/{repo}/pulls/{number}/files", max_pages=30):
            for f in page:
                patch = f.get("patch")
                ranges = [list(r) for r in base_side_ranges(patch)] if patch else None
                files[f["filename"]] = ranges
                previous = f.get("previous_filename")
                if previous:
                    files[previous] = ranges
    except RuntimeError:
        return None
    return files


def build(repo: str) -> dict:
    prs = []
    for page in gh_api_pages(f"repos/{repo}/pulls?state=open&sort=updated&direction=desc"):
        prs.extend(page)
    print(f"{repo}: {len(prs)} open PRs, fetching file lists ...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda pr: fetch_pr_files(repo, pr["number"]), prs))

    entries, failed = {}, []
    for pr, files in zip(prs, results, strict=True):
        if files is None:
            failed.append(pr["number"])
            continue
        entries[str(pr["number"])] = {
            "title": pr.get("title", ""),
            "author": (pr.get("user") or {}).get("login", "ghost"),
            "updated_at": pr.get("updated_at", ""),
            "draft": pr.get("draft", False),
            "files": files,
        }
    data = {
        "repo": repo,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "open_prs": len(prs),
        "failed": failed,
        "prs": entries,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(repo).write_text(json.dumps(data), encoding="utf-8")
    print(f"cached {len(entries)}/{len(prs)} PRs to {cache_path(repo)}; failed: {failed or 'none'}", file=sys.stderr)
    return data


def distance(line: int, ranges: list[list[int]]) -> int:
    """Lines between `line` and the nearest range; 0 when inside one."""
    best = None
    for start, end in ranges:
        d = 0 if start <= line <= end else min(abs(line - start), abs(line - end))
        best = d if best is None else min(best, d)
    return best if best is not None else 10**9


def verdict(line: int, ranges: list[list[int]] | None) -> str:
    if ranges is None:
        return "UNKNOWN (no patch from GitHub; read the PR diff)"
    d = distance(line, ranges)
    if d <= HARD_CONFLICT_LINES:
        return f"HARD CONFLICT ({d} lines away)"
    if d <= HOT_FILE_LINES:
        return f"HOT FILE ({d} lines away; check whether the other PR is near merging)"
    return f"DISTINCT ({d} lines away)"


def check(data: dict, path: str, lines: list[int]) -> None:
    hits = [(n, pr) for n, pr in data["prs"].items() if path in pr["files"]]
    if not hits:
        print(f"COLD   {path}  (no open PR touches it)")
    for n, pr in sorted(hits, key=lambda item: int(item[0])):
        ranges = pr["files"][path]
        shown = "no patch" if ranges is None else ", ".join(f"{a}-{b}" for a, b in ranges)
        draft = " draft" if pr.get("draft") else ""
        print(f"SHARED {path}  #{n}{draft} @{pr['author']} updated {pr['updated_at'][:10]}  base lines {shown}")
        print(f"        {pr['title'][:90]}")
        for line in lines:
            print(f"        your line {line}: {verdict(line, ranges)}")


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--build", action="store_true", help="fetch every open PR's file list and cache it")
    parser.add_argument("--check", action="append", default=[], metavar="PATH", help="repo-relative path; repeatable")
    parser.add_argument(
        "--line", action="append", type=int, default=[], help="base-branch line you plan to edit; repeatable"
    )
    args = parser.parse_args()

    if args.build:
        data = build(args.repo)
    else:
        try:
            data = json.loads(cache_path(args.repo).read_text(encoding="utf-8"))
        except FileNotFoundError:
            sys.exit(f"no cached map for {args.repo}; run with --build first")

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

    for path in args.check:
        check(data, path.replace("\\", "/"), args.line)


if __name__ == "__main__":
    main()
