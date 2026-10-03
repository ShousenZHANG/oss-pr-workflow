"""Check a drafted PR description against the repo's template, profile and your diff.

    python pr_body_check.py owner/repo --body pr_body.md [--template FILE] [--issue N] [--umbrella]
                            [--base upstream/main] [--evidence notes.md]

Run inside the clone on the feature branch. FAIL blocks showing the draft to
the user; WARN lines must each be confirmed true or fixed.

Checks:
  - every heading of the PR template is present
  - the template checklist is reproduced item for item (none dropped, none invented)
  - the AI-disclosure line required by the profile is present
  - the issue is linked; an umbrella issue is referenced, not closed (no Fixes/Closes)
  - no template placeholders left (<issue number>, [Tool Name], TODO)
  - backticked paths and identifiers exist in the diff or the repository
  - numbers in the prose appear in the diff or in an evidence file (claims like "returned 500")
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from _config import cache_dir, load_profile, profile_name
from _gh import git, use_utf8_stdout

CHECKBOX = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
PLACEHOLDER = re.compile(
    r"<issue[ _-]?number>|#issue-number|\[Tool Name\]|<Tool Name>|\bTODO\b|\bTBD\b|<!-- *fill", re.IGNORECASE
)
CLOSING = r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*:?\s*(?:[\w.-]+/[\w.-]+)?#{n}\b"


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def normalize(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def template_headings(template: str) -> list[str]:
    return [m.group(1) for line in strip_comments(template).splitlines() if (m := HEADING.match(line))]


def checklist(text: str) -> list[str]:
    return [m.group(1) for line in strip_comments(text).splitlines() if (m := CHECKBOX.match(line))]


def check_template(body: str, template: str) -> list[Finding]:
    findings = []
    body_norm = normalize(strip_comments(body))
    for heading in template_headings(template):
        if normalize(heading) and normalize(heading) not in body_norm:
            findings.append(Finding("FAIL", f"template heading missing: {heading!r}"))
    wanted = [normalize(item) for item in checklist(template)]
    have = [normalize(item) for item in checklist(body)]
    for item, raw in zip(wanted, checklist(template), strict=True):
        if item not in have:
            findings.append(Finding("FAIL", f"template checklist item missing or reworded: {raw[:90]!r}"))
    if wanted:
        for item, raw in zip(have, checklist(body), strict=True):
            if item not in wanted:
                findings.append(Finding("FAIL", f"checklist item not in the template (invented?): {raw[:90]!r}"))
    return findings


def check_links(body: str, issue: int | None, umbrella: bool, issue_required: bool) -> list[Finding]:
    findings = []
    if issue is None:
        if issue_required:
            findings.append(Finding("FAIL", "the repo requires a linked issue; pass --issue N and reference it"))
        return findings
    if not re.search(rf"#{issue}\b", body):
        findings.append(Finding("FAIL", f"issue #{issue} is not referenced"))
    elif umbrella and re.search(CLOSING.format(n=issue), body):
        findings.append(Finding("FAIL", f"umbrella issue #{issue} uses a closing keyword; use a plain reference"))
    return findings


def check_disclosure(body: str, facts: dict[str, str]) -> list[Finding]:
    pattern = facts.get("disclosure_regex", "")
    if not pattern or pattern.startswith("("):
        return [Finding("WARN", "profile has no disclosure_regex; check the AI disclosure by hand")]
    if not re.search(pattern, body):
        return [Finding("FAIL", f"required AI disclosure not found (profile disclosure_regex: {pattern})")]
    return []


def check_placeholders(body: str) -> list[Finding]:
    return [
        Finding("FAIL", f"template placeholder left in: {m.group(0)!r}")
        for m in PLACEHOLDER.finditer(strip_comments(body))
    ]


def backticked(body: str) -> list[str]:
    tokens = re.findall(r"`([^`\n]{2,80})`", strip_comments(body))
    return list(dict.fromkeys(t.strip() for t in tokens))


def looks_like_path(token: str) -> bool:
    return "/" in token or bool(re.search(r"\.(py|ts|tsx|js|go|rs|java|kt|rb|md|rst|ya?ml|toml|json)$", token))


def looks_like_identifier(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][\w.]*(\(\))?", token)) and not token.isupper()


def check_references(body: str, files: list[str], diff: str, repo_has) -> list[Finding]:
    findings = []
    for token in backticked(body):
        if looks_like_path(token):
            if token not in files and not repo_has(token, path=True):
                findings.append(Finding("WARN", f"path `{token}` is not in the diff or the repository"))
        elif looks_like_identifier(token):
            name = token.removesuffix("()").split(".")[-1]
            if name not in diff and not repo_has(name, path=False):
                findings.append(Finding("WARN", f"identifier `{token}` appears nowhere in the diff or the repository"))
    return findings


def check_numbers(body: str, diff: str, evidence: str) -> list[Finding]:
    prose = re.sub(r"`[^`]*`|#\d+|\b\d+\.\d+(?:\.\d+)*\b|\b\d{4}-\d{2}-\d{2}\b|https?://\S+", " ", strip_comments(body))
    numbers = sorted(set(re.findall(r"\b\d{2,}\b", prose)), key=int)
    return [
        Finding("WARN", f"number {n} in the description is not in the diff or the evidence file; verify the claim")
        for n in numbers
        if n not in diff and n not in evidence
    ]


def repo_has_factory():
    tracked = set(git(["ls-files"]).splitlines())

    def repo_has(token: str, path: bool) -> bool:
        if path:
            return token in tracked or any(
                t.endswith("/" + token) or t.startswith(token.rstrip("/") + "/") for t in tracked
            )
        result = subprocess.run(["git", "grep", "-q", "-F", token], capture_output=True)
        return result.returncode == 0

    return repo_has


def load_template(repo: str, explicit: str | None) -> str | None:
    if explicit:
        return Path(explicit).read_text(encoding="utf-8")
    cached = cache_dir() / "templates" / profile_name(repo)
    return cached.read_text(encoding="utf-8") if cached.exists() else None


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo (selects the profile)")
    parser.add_argument("--body", required=True, help="file holding the drafted PR description")
    parser.add_argument("--template", help="PR template file (default: cached by profile_draft.py)")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--umbrella", action="store_true", help="the issue is an umbrella that must stay open")
    parser.add_argument("--base", default="upstream/main")
    parser.add_argument("--evidence", help="notes file that backs numeric claims (test output, repro logs)")
    parser.add_argument("--no-git", action="store_true", help="skip checks that need the clone")
    args = parser.parse_args()

    body = Path(args.body).read_text(encoding="utf-8")
    facts, _rules, profile = load_profile(args.repo)
    findings: list[Finding] = []
    if profile is None:
        findings.append(Finding("WARN", f"no profile for {args.repo}; disclosure and issue rules unchecked"))
    template = load_template(args.repo, args.template)
    if template is None:
        findings.append(Finding("WARN", "no PR template found; run profile_draft.py or pass --template"))
    else:
        findings += check_template(body, template)
    findings += check_links(body, args.issue, args.umbrella, facts.get("issue_required", "").startswith("yes"))
    if profile is not None:
        findings += check_disclosure(body, facts)
    findings += check_placeholders(body)
    evidence = Path(args.evidence).read_text(encoding="utf-8") if args.evidence else ""
    if not args.no_git:
        files = [f for f in git(["diff", "--name-only", f"{args.base}...HEAD"]).splitlines() if f.strip()]
        diff = git(["diff", f"{args.base}...HEAD"])
        findings += check_references(body, files, diff, repo_has_factory())
        findings += check_numbers(body, diff, evidence)

    for finding in findings:
        print(f"{finding.level}: {finding.message}")
    failed = any(f.level == "FAIL" for f in findings)
    print("RESULT: " + ("FAIL" if failed else "PASS (confirm every WARN)" if findings else "PASS"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
