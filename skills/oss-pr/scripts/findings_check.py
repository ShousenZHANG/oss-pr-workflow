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
  - the change is committed: uncommitted edits to tracked files, or no change at all, FAIL
  - every changed file appears in coverage, deleted files included; a skipped file needs a reason
  - every finding points at a file in the diff and at lines that exist in the new file
    (the last version, for a deleted file); lines outside the changed hunks are a WARN
  - severity and category come from the fixed lists below
  - every finding is resolved: "fixed", or "dismissed: <proof>"
  - protected categories cannot be dismissed by argument alone: the proof must cite a test ("test:")
  - at least one test is proven, by its saved output, to fail without the fix and pass with it,
    unless the user approved a "test_waiver" {precedent, ci_coverage, approved_by_user: true}
    for code no local test can reach
  - categories: bug, security, performance, maintainability, test, style, documentation,
    behavior-change, compatibility, concurrency, unused-parameter, other
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

from _gh import (
    base_side_ranges,
    default_base,
    git,
    new_side_ranges,
    run_git_or_exit,
    split_diff,
    use_utf8_stdout,
    worktree_findings,
)

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
# A user-approved test waiver can stand in for a test on these, never on security or concurrency findings.
WAIVABLE = {"behavior-change", "compatibility", "unused-parameter"}
# A real failure, not a summary that merely contains the word ("fail 0", "0 failed", "test_error_path").
# Type checkers count too, for typing-only changes: pyrefly prints `ERROR ...` lines and `N diagnostics`,
# mypy `Found N errors`.
FAILED_COUNT = re.compile(
    r"(?im)\b[1-9]\d* (?:failed|failing|failures?|errors?|diagnostics?)\b|\bfail(?:ed|ures?)?:? [1-9]\d*\b|"
    r"^\s*(?:FAILED\b|FAIL\b|ERROR\b|--- FAIL|not ok\b|Traceback \(most recent call last\)|panic:|AssertionError|✕|×)"
)
# One test's own result line, in the formats of pytest -v, unittest -v, go test -v, cargo, jest/vitest, surefire.
FAIL_LINE = re.compile(r"(?i)\bfail(?:ed|ures?|s)?\b|\berror\b|✕|×|✗|\bnot ok\b|<<< FAILURE")
PASS_LINE = re.compile(r"(?i)\bpass(?:ed|es)?\b|\bok\b|✓|✔|√")
SKIP_LINE = re.compile(r"(?i)\bskip(?:ped)?\b|\bx(?:fail|pass)\w*|\bpending\b|\btodo\b")


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def changed_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """path -> ranges of changed hunks, from `git diff -U0` output.

    New-side lines for added and modified files; old-side lines for a deleted file,
    which has no new side but still has to be reviewed.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    for old, new, body in split_diff(diff):
        if new is not None:
            result[new] = new_side_ranges("\n".join(body))
        elif old is not None:
            result[old] = base_side_ranges("\n".join(body))
    return result


def read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def result_lines(log: str, name: str) -> list[str]:
    """Lines of a run log that name this test as a whole word (`test_x`, not `test_x_slow`)."""
    word = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
    return [line for line in log.splitlines() if word.search(line)]


def check_tests(tests: list[dict], read_text=read_file) -> list[Finding]:
    """At least one test must be shown, by saved output, to fail without the fix and pass with it.

    A boolean the reviewer sets is not evidence; the two run logs are, and both must
    name the same test on its own result line: another test failing before, or
    another test passing (or this one skipped) after, proves nothing about this one.
    """
    out: list[Finding] = []
    proven = False
    for test in tests:
        name = test.get("name", "?")
        short = name.split("::")[-1].split("[")[0]
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
        if without_log.strip() == with_log.strip():
            out.append(Finding("FAIL", f"test {name}: the two logs are identical; they cannot be two different runs"))
            continue
        if not FAILED_COUNT.search(without_log):
            out.append(
                Finding("FAIL", f"test {name}: without_fix_log shows no failure (a zero failure count is a pass)")
            )
            continue
        if FAILED_COUNT.search(with_log):
            out.append(Finding("FAIL", f"test {name}: with_fix_log does not show a clean pass"))
            continue
        if not any(FAIL_LINE.search(line) for line in result_lines(without_log, short)):
            out.append(
                Finding(
                    "FAIL",
                    f"test {name}: without_fix_log does not show {short} failing; its name must be on a failure "
                    "line (a collection error or another test's failure is not this test failing)",
                )
            )
            continue
        passing = [
            line
            for line in result_lines(with_log, short)
            if PASS_LINE.search(line) and not FAIL_LINE.search(line) and not SKIP_LINE.search(line)
        ]
        if not passing:
            out.append(
                Finding(
                    "FAIL",
                    f"test {name}: with_fix_log does not show {short} passing; run it verbose (pytest -v, "
                    "go test -v, jest --verbose) so the result line names the test",
                )
            )
            continue
        proven = True
    if not proven:
        out.append(Finding("FAIL", "no test is proven to fail without the fix and pass with it"))
    return out


def merge_near(spans: list[tuple[int, int]], gap: int = 3) -> list[tuple[int, int]]:
    """Join hunks closer than `gap` lines, so a finding spanning two adjacent hunks counts as inside them."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def waiver_ok(waiver: dict | None) -> bool:
    """A user-approved exception for code no local test can reach (end-to-end only, missing toolchain).

    It must cite a merged precedent that shipped the same kind of change without a
    unit test, say what CI will exercise, and record that the user approved it.
    """
    return bool(
        waiver
        and str(waiver.get("precedent", "")).strip()
        and str(waiver.get("ci_coverage", "")).strip()
        and waiver.get("approved_by_user") is True
    )


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

    waiver = report.get("test_waiver")
    test_results = check_tests(report.get("tests", []), read_text)
    if waiver_ok(waiver) and any(r.level == "FAIL" for r in test_results):
        test_results = [r for r in test_results if r.level != "FAIL"] + [
            Finding(
                "WARN",
                f"test requirement waived by the user: precedent {waiver['precedent']}; "
                f"CI coverage: {waiver['ci_coverage']}. Say so in the PR.",
            )
        ]
    elif waiver and not waiver_ok(waiver):
        test_results.append(Finding("FAIL", "test_waiver needs precedent, ci_coverage and approved_by_user: true"))
    out += test_results
    for entry in report.get("not_run", []):
        if not str(entry.get("user_choice", "")).strip():
            out.append(
                Finding(
                    "FAIL",
                    f"not_run {entry.get('check', '?')!r}: record the user's choice (install the tool, or accept CI "
                    "as the first run) in user_choice",
                )
            )
    merged_ranges = {path: merge_near(spans) for path, spans in ranges.items()}

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
        elif not any(a - 3 <= start and end <= b + 3 for a, b in merged_ranges.get(path, [])):
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
            elif category in PROTECTED and "test:" not in proof and not (waiver_ok(waiver) and category in WAIVABLE):
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
    files = [f for f in run_git_or_exit(["diff", "--name-only", "-z", f"{base}...HEAD"]).split("\0") if f]
    deleted = {
        f for f in run_git_or_exit(["diff", "--name-only", "-z", "--diff-filter=D", f"{base}...HEAD"]).split("\0") if f
    }
    ranges = changed_ranges(run_git_or_exit(["diff", "-U0", f"{base}...HEAD"]))
    merge_base = run_git_or_exit(["merge-base", base, "HEAD"]).strip()
    line_counts = {}
    for f in files:
        # A deleted file's findings point at its last version, on the base side.
        revision = merge_base if f in deleted else "HEAD"
        try:
            line_counts[f] = len(git(["show", f"{revision}:{f}"]).splitlines())
        except RuntimeError:
            line_counts[f] = 0
    results = [Finding(level, message) for level, message in worktree_findings()]
    if not files:
        results.append(Finding("FAIL", f"no committed change against {base}; there is nothing to review"))
    results += validate(report, files, ranges, line_counts)
    for r in results:
        print(f"{r.level}: {r.message}")
    failed = any(r.level == "FAIL" for r in results)
    print("RESULT: " + ("FAIL" if failed else "PASS"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
