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
from datetime import datetime, timedelta, timezone

RETRIES = 4
RETRY_DELAY_SECONDS = 4

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


def is_bot(login: str, user_type: str = "") -> bool:
    """GitHub App accounts have type Bot; many project bots are plain users named like `HaystackBot`."""
    return user_type == "Bot" or login.lower().endswith("bot") or login.endswith("[bot]")


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
