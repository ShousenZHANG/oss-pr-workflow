"""Validate a self-review report before the change may move on to submission.

    python findings_check.py review.json [--base upstream/main]

Run inside the clone on the feature branch. The report is JSON:

{
  "coverage": [{"path": "src/a.py", "status": "reviewed"},
               {"path": "docs/x.md", "status": "skipped", "reason": "docs only, no behavior"}],
  "tests":    [{"name": "test_empty_messages", "fails_without_fix": true}],
  "findings": [{"path": "src/a.py", "start_line": 40, "end_line": 42,
                "severity": "high", "category": "behavior-change",
                "evidence": "old code returned 400 for ...",
                "resolution": "fixed"}]
}

Rules (exit status 1 on any FAIL):
  - every changed file appears in coverage; a skipped file needs a reason
  - every finding points at a file in the diff and at lines that exist in the new file;
    lines outside the changed hunks are a WARN (comments should be about changed code)
  - severity and category come from the fixed lists below
  - every finding is resolved: "fixed", or "dismissed: <proof>"
  - protected categories cannot be dismissed by argument alone: the proof must cite a test ("test:")
  - at least one test is recorded as failing without the fix (no test that passes either way)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from _gh import git, new_side_ranges, use_utf8_stdout

SEVERITIES = {"critical", "high", "medium", "low"}
CATEGORIES = {
    "bug",
    "security",
    "performance",
    "maintainability",
    "test",
    "style",
    "documentation",
    "behavior-change",
    "compatibility",
    "concurrency",
    "unused-parameter",
    "other",
}
PROTECTED = {"behavior-change", "compatibility", "concurrency", "unused-parameter", "security"}


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def changed_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """path -> new-side ranges of changed hunks, from `git diff -U0` output."""
    result: dict[str, list[tuple[int, int]]] = {}
    path = None
    chunk: list[str] = []

    def flush() -> None:
        if path is not None:
            result[path] = new_side_ranges("\n".join(chunk))

    for line in diff.splitlines():
        if line.startswith("+++ "):
            flush()
            target = line[4:]
            path = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            chunk = []
        elif line.startswith("@@"):
            chunk.append(line)
    flush()
    return result


def validate(report: dict, files: list[str], ranges: dict, line_counts: dict[str, int]) -> list[Finding]:
    out: list[Finding] = []
    coverage = {c.get("path"): c for c in report.get("coverage", [])}
    for f in files:
        entry = coverage.get(f)
        if entry is None:
            out.append(Finding("FAIL", f"{f}: not in coverage; review it or skip it with a reason"))
        elif entry.get("status") == "skipped" and not entry.get("reason"):
            out.append(Finding("FAIL", f"{f}: skipped without a reason"))
        elif entry.get("status") not in {"reviewed", "skipped"}:
            out.append(Finding("FAIL", f"{f}: coverage status must be reviewed or skipped"))
    reviewed = sum(1 for f in files if coverage.get(f, {}).get("status") == "reviewed")

    tests = report.get("tests", [])
    if not any(t.get("fails_without_fix") is True for t in tests):
        out.append(Finding("FAIL", "no test recorded as failing without the fix; prove the test can fail"))

    for i, item in enumerate(report.get("findings", []), 1):
        tag = f"finding {i} ({item.get('path')}:{item.get('start_line')})"
        path = item.get("path")
        if path not in files:
            out.append(Finding("FAIL", f"{tag}: file is not part of the diff"))
            continue
        start, end = item.get("start_line"), item.get("end_line") or item.get("start_line")
        count = line_counts.get(path, 0)
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= count:
            out.append(Finding("FAIL", f"{tag}: line numbers do not exist in the new file"))
        elif not any(a - 3 <= start and end <= b + 3 for a, b in ranges.get(path, [])):
            out.append(Finding("WARN", f"{tag}: lines are outside the changed hunks"))
        if item.get("severity") not in SEVERITIES:
            out.append(Finding("FAIL", f"{tag}: severity must be one of {sorted(SEVERITIES)}"))
        category = item.get("category")
        if category not in CATEGORIES:
            out.append(Finding("FAIL", f"{tag}: category must be one of {sorted(CATEGORIES)}"))
        resolution = (item.get("resolution") or "").strip()
        if resolution != "fixed" and not resolution.startswith("dismissed:"):
            out.append(Finding("FAIL", f"{tag}: unresolved; write 'fixed' or 'dismissed: <proof>'"))
        elif resolution.startswith("dismissed:"):
            proof = resolution[len("dismissed:") :].strip()
            if not proof:
                out.append(Finding("FAIL", f"{tag}: dismissed without proof"))
            elif category in PROTECTED and "test:" not in proof:
                out.append(Finding("FAIL", f"{tag}: {category} can only be dismissed with a test ('test: <name>')"))
    out.append(Finding("INFO", f"coverage {reviewed}/{len(files)} files reviewed"))
    return out


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", help="self-review JSON")
    parser.add_argument("--base", default="upstream/main")
    args = parser.parse_args()

    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)
    files = [f for f in git(["diff", "--name-only", "--diff-filter=d", f"{args.base}...HEAD"]).splitlines() if f]
    ranges = changed_ranges(git(["diff", "-U0", f"{args.base}...HEAD"]))
    line_counts = {}
    for f in files:
        try:
            line_counts[f] = len(git(["show", f"HEAD:{f}"]).splitlines())
        except RuntimeError:
            line_counts[f] = 0
    results = validate(report, files, ranges, line_counts)
    for r in results:
        print(f"{r.level}: {r.message}")
    failed = any(r.level == "FAIL" for r in results)
    print("RESULT: " + ("FAIL" if failed else "PASS"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
