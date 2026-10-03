"""Shared helpers: a retrying wrapper around the `gh` CLI and small parsers.

Only the standard library is used, so the scripts run wherever `gh` is
installed and authenticated (`gh auth status`).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache

RETRIES = 4
RETRY_DELAY_SECONDS = 4
API_FLOOR = 500  # stop and report when fewer REST calls than this remain

# Associations GitHub gives to people without write access to the repository.
EXTERNAL_ASSOCIATIONS = frozenset({"CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"})

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def use_utf8_stdout() -> None:
    """Avoid UnicodeEncodeError on Windows consoles that default to a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def gh(args: list[str]) -> str | None:
    """Run `gh <args>` and return stdout, retrying transient failures. None means every attempt failed."""
    last_error = ""
    for attempt in range(RETRIES):
        result = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            return result.stdout
        last_error = result.stderr.strip()
        if "Not Found" in last_error or "HTTP 404" in last_error:
            break
        if attempt < RETRIES - 1:
            time.sleep(RETRY_DELAY_SECONDS)
    print(f"gh {' '.join(args[:3])} ... failed: {last_error[:200]}", file=sys.stderr)
    return None


def gh_json(args: list[str]):
    """Run `gh` and parse JSON output. None when the call failed or returned nothing."""
    out = gh(args)
    if not out or not out.strip():
        return None
    return json.loads(out)


def gh_api_pages(path: str, max_pages: int = 100):
    """Yield items from a paginated REST list endpoint, one page at a time, so callers can stop early."""
    separator = "&" if "?" in path else "?"
    for page in range(1, max_pages + 1):
        items = gh_json(["api", f"{path}{separator}per_page=100&page={page}"])
        if items is None:
            raise RuntimeError(f"failed to fetch page {page} of {path}")
        yield items
        if len(items) < 100:
            return


def gh_graphql(query: str, **variables: str):
    """Run a GraphQL query; None on failure."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F", f"{key}={value}"]
    return gh_json(args)


@lru_cache(maxsize=1)
def current_login() -> str:
    user = gh_json(["api", "user"]) or {}
    return user.get("login", "")


def api_remaining() -> int | None:
    data = gh_json(["api", "rate_limit"])
    if not data:
        return None
    return data["resources"]["core"]["remaining"]


def check_api_budget() -> None:
    """Exit with a clear message instead of failing halfway when the REST quota is nearly spent."""
    remaining = api_remaining()
    if remaining is not None and remaining < API_FLOOR:
        sys.exit(f"STOP: only {remaining} GitHub API calls left this hour (floor {API_FLOOR}); wait for the reset")


def truncation_warning(count: int, limit: int, what: str) -> str | None:
    """A result count equal to the requested limit usually means the list was cut off."""
    if count >= limit:
        return f"WARNING: {what} returned {limit} items, the limit; the list is probably truncated, not complete"
    return None


def raw_file(repo: str, path: str, ref: str = "HEAD") -> str | None:
    """Fetch a file from raw.githubusercontent.com (no API quota); fall back to the contents API."""
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return gh(["api", f"repos/{repo}/contents/{path}", "-H", "Accept: application/vnd.github.raw"])


KNOWN_BOT_NAMES = (
    "copilot",
    "coderabbit",
    "dosu",
    "gemini-code-assist",
    "sourcery",
    "codecov",
    "netlify",
    "vercel",
    "boring-cyborg",
    "cla-assistant",
    "claassistant",
    "sonarcloud",
    "mergify",
)


def is_bot(login: str, user_type: str = "") -> bool:
    """GitHub App accounts have type Bot; many project bots are plain users named like `HaystackBot`.

    Review bots often appear without the [bot] suffix in `gh pr view` output
    (e.g. `copilot-pull-request-reviewer`), so known names are matched too.
    """
    lower = login.lower()
    return (
        user_type == "Bot"
        or lower.endswith("bot")
        or login.endswith("[bot]")
        or any(name in lower for name in KNOWN_BOT_NAMES)
    )


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def days_ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def base_side_ranges(patch: str) -> list[tuple[int, int]]:
    """Return the base-branch line ranges each hunk of a unified diff touches.

    Base-side numbers are used because two open PRs are both written against
    the base branch, so their base-side ranges are directly comparable. A pure
    insertion (`-a,0`) is reported as the single line it is inserted after.
    """
    ranges = []
    for match in _HUNK_HEADER.finditer(patch or ""):
        start = int(match.group(1))
        length = int(match.group(2)) if match.group(2) is not None else 1
        end = start + max(length, 1) - 1
        ranges.append((start, end))
    return ranges


def base_changed_ranges(patch: str) -> list[tuple[int, int]]:
    """Base-branch lines a patch actually changes, without the context lines around each hunk.

    A removed line counts as itself; an insertion counts as the base line it is
    inserted before. Consecutive lines are merged into one range. Hunk headers
    alone overstate a change by up to three context lines on each side.
    """
    touched: list[int] = []
    base = 0
    replacing = False  # inside a run of removed lines: added lines replace them, not a new insertion
    for line in (patch or "").splitlines():
        header = _HUNK_HEADER.match(line)
        if header:
            base = int(header.group(1))
            if header.group(2) == "0":
                base += 1  # `-k,0` means "insert after line k"
            replacing = False
            continue
        if not base or line.startswith("\\"):
            continue
        if line.startswith("-"):
            touched.append(base)
            base += 1
            replacing = True
        elif line.startswith("+"):
            if not replacing:
                touched.append(base)
        else:
            base += 1
            replacing = False
    ranges: list[tuple[int, int]] = []
    for number in sorted(set(touched)):
        if ranges and number <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], number)
        else:
            ranges.append((number, number))
    return ranges


def new_side_ranges(patch: str) -> list[tuple[int, int]]:
    """New-file line ranges each hunk touches; a pure deletion (`+c,0`) yields no range."""
    ranges = []
    for match in _HUNK_HEADER.finditer(patch or ""):
        start = int(match.group(3))
        length = int(match.group(4)) if match.group(4) is not None else 1
        if length:
            ranges.append((start, start + length - 1))
    return ranges


def git(args: list[str], cwd: str | None = None) -> str:
    """Run a local git command and return stdout; raise on failure."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def default_base() -> str:
    """The upstream default branch as a ref (`upstream/master`, `origin/main`, ...), detected from the clone.

    Repositories differ (mlflow uses master), so scripts must not assume `main`.
    Falls back to `upstream/main` when nothing can be detected.
    """
    for remote in ("upstream", "origin"):
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        for branch in ("main", "master"):
            probe = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"], capture_output=True
            )
            if probe.returncode == 0:
                return f"{remote}/{branch}"
    return "upstream/main"


def run_git_or_exit(args: list[str]) -> str:
    """git() for command-line entry points: print a clean message instead of a traceback."""
    try:
        return git(args)
    except RuntimeError as error:
        sys.exit(f"ERROR: {error}\nHint: run inside the clone, and pass --base <remote>/<default-branch> if needed.")
