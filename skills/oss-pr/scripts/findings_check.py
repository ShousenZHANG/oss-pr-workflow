"""Validate a self-review report before the change may move on to submission.

    python findings_check.py review.json [--base REF]

Run inside the clone on the feature branch (--base defaults to the detected
upstream default branch). The report is JSON:

{
  "reviewer": "subagent",
  "coverage": [{"path": "src/a.py", "status": "reviewed"},
               {"path": "docs/x.md", "status": "skipped", "reason": "docs only, no behavior"}],
  "tests":    [{"name": "test_empty_messages", "fails_without_fix": true,
                "command": "pytest tests/test_a.py -k empty",
                "without_fix_log": "runs/without_fix.txt", "with_fix_log": "runs/with_fix.txt"}],
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
  - at least one test is proven, by its saved output, to fail without the fix and pass with it
  - categories: bug, security, performance, maintainability, test, style, documentation,
    behavior-change, compatibility, concurrency, unused-parameter, other
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

from _gh import default_base, git, new_side_ranges, run_git_or_exit, use_utf8_stdout

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
FAIL_MARKER = re.compile(r"(?i)\b(?:fail(?:ed|ure|s)?|error|traceback|assert\w*|panic(?:ked)?|exception)\b")
FAILED_COUNT = re.compile(r"(?im)\b[1-9]\d* (?:failed|failures?|errors?)\b|^FAILED\b|^--- FAIL|^not ok\b")
PASS_MARKER = re.compile(r"(?i)\b(?:passed|pass|ok|success(?:ful)?)\b")


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


def read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def check_tests(tests: list[dict], read_text=read_file) -> list[Finding]:
    """At least one test must be shown, by saved output, to fail without the fix and pass with it.

    A boolean the reviewer sets is not evidence; the two run logs are.
    """
    out: list[Finding] = []
    proven = False
    for test in tests:
        name = test.get("name", "?")
        if test.get("fails_without_fix") is not True:
            continue
        without_log = read_text(test.get("without_fix_log", "")) if test.get("without_fix_log") else None
        with_log = read_text(test.get("with_fix_log", "")) if test.get("with_fix_log") else None
        if without_log is None or with_log is None:
            out.append(
                Finding(
                    "FAIL",
                    f"test {name}: save the runs and give without_fix_log and with_fix_log (paths to the output)",
                )
            )
            continue
        if not FAIL_MARKER.search(without_log):
            out.append(Finding("FAIL", f"test {name}: without_fix_log shows no failure"))
            continue
        if FAILED_COUNT.search(with_log) or not PASS_MARKER.search(with_log):
            out.append(Finding("FAIL", f"test {name}: with_fix_log does not show a clean pass"))
            continue
        if name.split("::")[-1].split("[")[0] not in without_log:
            out.append(Finding("WARN", f"test {name}: its name does not appear in without_fix_log; was it this test?"))
        proven = True
    if not proven:
        out.append(Finding("FAIL", "no test is proven to fail without the fix and pass with it"))
    return out


def validate(report: dict, files: list[str], ranges: dict, line_counts: dict[str, int], read_text=read_file):
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

    out += check_tests(report.get("tests", []), read_text)

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
    parser.add_argument("--base", help="base ref (default: detected upstream default branch)")
    args = parser.parse_args()
    base = args.base or default_base()

    try:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        sys.exit(f"ERROR: cannot read {args.report}: {error}")
    files = [f for f in run_git_or_exit(["diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"]).splitlines() if f]
    ranges = changed_ranges(run_git_or_exit(["diff", "-U0", f"{base}...HEAD"]))
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
