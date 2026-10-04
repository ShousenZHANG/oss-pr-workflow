"""Check a drafted PR description against the repo's template, profile and your diff.

    python pr_body_check.py owner/repo --body pr_body.md [--template FILE] [--issue N] [--umbrella]
                            [--base REF] [--evidence notes.md]

Run inside the clone on the feature branch. FAIL blocks showing the draft to
the user; WARN lines must each be confirmed true or fixed. --base defaults to
the upstream default branch detected from the clone.

Checks:
  - every heading of the PR template is present (markdown `#` headings and bold-only lines)
  - the template checklist is reproduced item for item (none dropped, none invented;
    a note appended after an item is fine)
  - the AI-disclosure line required by the profile is present (unless it goes in a commit trailer)
  - the issue is linked; an umbrella issue is referenced, not closed (no Fixes/Closes)
  - no template placeholders or instruction text left in
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
from _gh import default_base, run_git_or_exit, use_utf8_stdout

CHECKBOX = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
BOLD_HEADING = re.compile(r"^\s*\*\*([^*]{3,}?)\*\*\s*:?\s*$")
PLACEHOLDER = re.compile(
    r"<issue[ _-]?number>|#issue-number|\[Tool Name\]|<Tool Name>|\bTODO\b|\bTBD\b|<!-- *fill", re.IGNORECASE
)
TEMPLATE_INSTRUCTION = re.compile(r"\[[^\]\n]{12,}\](?!\()")
CLOSING = r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*:?\s*(?:[\w.-]+/[\w.-]+)?#{n}\b"
UNFILLED = re.compile(r"(?i)fill in|^\s*$")
NOT_REQUIRED = re.compile(r"(?i)^\s*(?:none|not required|n/?a)\b")


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
    headings = []
    for line in strip_comments(template).splitlines():
        match = HEADING.match(line) or BOLD_HEADING.match(line)
        if match:
            headings.append(match.group(1))
    return headings


def checklist(text: str) -> list[str]:
    return [m.group(1) for line in strip_comments(text).splitlines() if (m := CHECKBOX.match(line))]


def same_item(template_item: str, body_item: str) -> bool:
    """Equal, or the body item is the template item with a note appended."""
    return body_item == template_item or body_item.startswith(template_item + " ")


def check_template(body: str, template: str) -> list[Finding]:
    findings = []
    body_norm = normalize(strip_comments(body))
    for heading in template_headings(template):
        if normalize(heading) and normalize(heading) not in body_norm:
            findings.append(Finding("FAIL", f"template heading missing: {heading!r}"))
    wanted = [(normalize(item), item) for item in checklist(template)]
    have = [(normalize(item), item) for item in checklist(body)]
    for norm, raw in wanted:
        if not any(same_item(norm, h) for h, _ in have):
            findings.append(Finding("FAIL", f"template checklist item missing or reworded: {raw[:90]!r}"))
    if wanted:
        for norm, raw in have:
            if not any(same_item(w, norm) for w, _ in wanted):
                findings.append(Finding("FAIL", f"checklist item not in the template (invented?): {raw[:90]!r}"))
    for instruction in template_instructions(template):
        if instruction in body:
            findings.append(Finding("FAIL", f"template instruction text left in: {instruction[:80]!r}"))
    return findings


def template_instructions(template: str) -> list[str]:
    """Bracketed placeholder text the author must replace, e.g. `[Add information on how this was tested]`.

    Reference-style link labels (`[Submitting a pull request]` with a `[...]: url`
    definition) and text inside checklist items are part of the template's wording,
    not placeholders, so they are excluded.
    """
    text = strip_comments(template)
    defined = {label for label in re.findall(r"(?m)^\s*\[([^\]]+)\]:\s*\S+", text)}
    prose = "\n".join(line for line in text.splitlines() if not CHECKBOX.match(line))
    return [
        item
        for item in TEMPLATE_INSTRUCTION.findall(prose)
        if item[1:-1] not in defined and not re.match(r"^\s*\[[^\]]+\]:", item)
    ]


def check_release_block(body: str, template: str) -> list[Finding]:
    """A template's ```release-note block must be filled in; deleting it is allowed only if the template says so."""
    if not re.search(r"```\s*release-notes?\b", template):
        return []
    block = re.search(r"```\s*release-notes?\b(.*?)```", body, re.DOTALL)
    if block is None:
        if re.search(r"(?i)remove (?:this|the) release[- ]notes?", template):
            return []
        return [Finding("FAIL", "the template's ```release-note block is missing from the body")]
    if not strip_comments(block.group(1)).strip():
        return [Finding("FAIL", "the ```release-note block is empty; write the note or follow the template's rule")]
    return []


def check_attestations(body: str) -> list[Finding]:
    """Ticked boxes are claims. Statements about the person ("I have read ...") and claims of verification
    ("works as expected", "tested") must be confirmed, by the user or by evidence, before submitting."""
    findings = []
    for line in strip_comments(body).splitlines():
        match = re.match(r"^\s*[-*]\s*\[[xX]\]\s*(.+?)\s*$", line)
        if not match:
            continue
        item = match.group(1)
        if re.match(r"(?i)(?:i\b|i'(?:ve|m)\b|my\b|we\b)", item):
            findings.append(Finding("WARN", f"ticked statement about the user, confirm with them: {item[:80]!r}"))
        elif re.search(r"(?i)\b(?:works?|tested|verified|checked|expected|passes|reviewed|documented)\b", item):
            findings.append(Finding("WARN", f"ticked claim, confirm it was actually done: {item[:80]!r}"))
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
    """The profile's disclosure_regex must match the body, unless disclosure belongs in commit trailers only."""
    pattern = facts.get("disclosure_regex", "")
    location = facts.get("disclosure_location", "body")
    if NOT_REQUIRED.match(pattern):
        return []
    if UNFILLED.search(pattern):
        return [Finding("FAIL", "profile disclosure_regex is not filled in; finish the profile first")]
    if location.strip().startswith("commit-trailer"):
        return [Finding("INFO", "disclosure goes in commit trailers here; diff_check.py checks it")]
    try:
        regex = re.compile(pattern)
    except re.error as error:
        return [Finding("FAIL", f"profile disclosure_regex is not a valid regex: {error}")]
    if not regex.search(body):
        return [Finding("FAIL", f"required AI disclosure not found (profile disclosure_regex: {pattern})")]
    return []


def check_placeholders(body: str) -> list[Finding]:
    return [
        Finding("FAIL", f"template placeholder left in: {m.group(0)!r}")
        for m in PLACEHOLDER.finditer(strip_comments(body))
    ]


def backticked(text: str) -> list[str]:
    tokens = re.findall(r"`([^`\n]{2,80})`", strip_comments(text))
    return list(dict.fromkeys(t.strip() for t in tokens))


def looks_like_path(token: str) -> bool:
    return "/" in token or bool(re.search(r"\.(py|ts|tsx|js|go|rs|java|kt|rb|md|rst|ya?ml|toml|json)$", token))


def looks_like_identifier(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][\w.]*(\(\))?", token)) and not token.isupper()


def check_references(body: str, files: list[str], diff: str, repo_has, template: str = "") -> list[Finding]:
    findings = []
    from_template = set(backticked(template))
    for token in backticked(body):
        if token in from_template:
            continue
        if looks_like_path(token):
            if token not in files and not repo_has(token, path=True):
                findings.append(Finding("WARN", f"path `{token}` is not in the diff or the repository"))
        elif looks_like_identifier(token):
            name = token.removesuffix("()").split(".")[-1]
            if name not in diff and not repo_has(name, path=False):
                findings.append(Finding("WARN", f"identifier `{token}` appears nowhere in the diff or the repository"))
    return findings


def check_numbers(body: str, diff: str, evidence: str, template: str = "") -> list[Finding]:
    prose = re.sub(r"`[^`]*`|#\d+|\b\d+\.\d+(?:\.\d+)*\b|\b\d{4}-\d{2}-\d{2}\b|https?://\S+", " ", strip_comments(body))
    numbers = sorted(set(re.findall(r"\b\d{2,}\b", prose)), key=int)
    return [
        Finding("WARN", f"number {n} in the description is not in the diff or the evidence file; verify the claim")
        for n in numbers
        if n not in diff and n not in evidence and n not in template
    ]


def repo_has_factory():
    tracked = set(run_git_or_exit(["ls-files"]).splitlines())

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
    parser.add_argument("--base", help="base ref (default: detected upstream default branch)")
    parser.add_argument("--evidence", help="notes file that backs numeric claims (test output, repro logs)")
    parser.add_argument("--no-git", action="store_true", help="skip checks that need the clone")
    args = parser.parse_args()

    body_path = Path(args.body)
    if not body_path.exists():
        sys.exit(f"ERROR: body file not found: {body_path}")
    body = body_path.read_text(encoding="utf-8")
    facts, _rules, profile = load_profile(args.repo)
    findings: list[Finding] = []
    if profile is None:
        findings.append(Finding("WARN", f"no profile for {args.repo}; disclosure and issue rules unchecked"))
    template = load_template(args.repo, args.template)
    if template is None:
        findings.append(Finding("WARN", "no PR template found; run profile_draft.py or pass --template"))
    else:
        findings += check_template(body, template)
        findings += check_release_block(body, template)
    findings += check_attestations(body)
    findings += check_links(body, args.issue, args.umbrella, facts.get("issue_required", "").startswith("yes"))
    if profile is not None:
        findings += check_disclosure(body, facts)
    findings += check_placeholders(body)
    evidence = Path(args.evidence).read_text(encoding="utf-8") if args.evidence else ""
    if not args.no_git:
        base = args.base or default_base()
        files = [f for f in run_git_or_exit(["diff", "--name-only", f"{base}...HEAD"]).splitlines() if f.strip()]
        diff = run_git_or_exit(["diff", f"{base}...HEAD"])
        findings += check_references(body, files, diff, repo_has_factory(), template or "")
        findings += check_numbers(body, diff, evidence, template or "")

    for finding in findings:
        print(f"{finding.level}: {finding.message}")
    failed = any(f.level == "FAIL" for f in findings)
    warned = any(f.level == "WARN" for f in findings)
    print("RESULT: " + ("FAIL" if failed else "PASS (confirm every WARN)" if warned else "PASS"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
