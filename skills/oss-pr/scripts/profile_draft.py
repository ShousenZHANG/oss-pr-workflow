"""Draft a repository profile from the repo's own documents and recent merged PRs.

    python profile_draft.py owner/repo [--stdout] [--sample 15]

Reads the PR template(s), CONTRIBUTING, AGENTS.md / CLAUDE.md, AI-policy files,
contributing-docs pages and CI workflows (via raw.githubusercontent.com, no API
quota), plus a sample of recently merged outside PRs. Writes a draft to
~/.oss-pr/repos/<owner>__<repo>.md (or <...>.draft.md when a profile exists).

Every detected fact is a heuristic and is marked "(auto, verify)". A human must
read the quoted evidence and correct the Facts block before the profile is used.
The AI-policy level errs toward the stricter reading when signals conflict.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from _config import AI_POLICY_LEVELS, cache_dir, data_dir, profile_name
from _gh import EXTERNAL_ASSOCIATIONS, check_api_budget, gh_json, is_bot, raw_file, use_utf8_stdout

DOC_NAME = re.compile(
    r"(?i)(^|/)(contributing|agents|claude|ai[_-]?policy|ai[_-]?usage|ai[_-]?guidelines|"
    r"pull_request_template|code_of_conduct_for_ai)\.(md|rst|txt)$"
)
CONTRIB_DOC_DIR = re.compile(r"(?i)^(contributing-docs|docs/contributing|\.github/contributing)/")
CONTRIB_DOC_TOPIC = re.compile(r"(?i)(pull.?request|ai|gen.?ai|open.?pull|limit|commit|review)")
TEMPLATE_PATH = re.compile(r"(?i)^(\.github/|docs/)?pull_request_template(\.md|/[^/]+\.md)$")
WORKFLOW_PATH = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")

AI_TERM = re.compile(
    r"\bAI\b|\bA\.I\.|(?i:\bLLMs?\b|large language model|\bgenerative\b|\bgen-?ai\b|copilot|chatgpt|\bclaude\b|"
    r"\bcodex\b|cod(?:e|ing) agents?|coding assistants?|\bai agents?|autonomous agents?|"
    r"agent-?(?:generated|authored|driven|written)|"
    r"machine[- ]generated|automated agents?|\bagentic\b|\bagents? (?:should|must|may|are|is)\b)"
)
BAN = re.compile(
    r"(?i)\b(?:do not|don't|must not|may not|will not|won't|never|not)\s+(?:be\s+)?"
    r"(?:accept\w*|allow\w*|permit\w*|use|submit|welcome\w*|prepare|open|create|send|make)\b"
    r"|\b(?:prohibit\w*|forbid\w*|banned|not allowed|not permitted|not accepted)\b"
)
AUTONOMOUS = re.compile(r"(?i)\b(?:autonomous\w*|fully automated|without (?:a )?human|unsupervised|unreviewed)\b")
ISSUE_CLASS = re.compile(
    r"(?i)\b(?:good first issues?|help wanted|unsolicited|acceptance criteria|labell?ed|only (?:on|for) issues?)\b"
)
DISCLOSE = re.compile(
    r"(?i)\b(?:disclos\w*|declare|state that|indicate|generated-by|assisted-by|influence level|"
    r"tick the|disclaimer|final line)\b|\badd\b[^`]*`[^`]+`"
)
ALLOW = re.compile(
    r"(?i)\b(?:welcome|allowed|permitted|you (?:may|can) use|is fine|fine to use|okay to use|ok to use)\b"
)
AGENT_PR_BAN = re.compile(
    r"(?i)\b(?:do not|don't|never|must not)\s+(?:prepare|open|submit|create|send|file)\b[^.]{0,80}?"
    r"\b(?:pull requests?|PRs?|contributions?)\b"
)
NEWCOMER = re.compile(
    r"(?i)\b(?:first[- ]time contributors?|new contributors?|first contributions?|newcomers?)\b"
    r"|\b(?:before|until)\b[^.]{0,40}\bmerged\b"
)
AGENT_DOC = re.compile(r"(?i)(^|/)(AGENTS|CLAUDE)\.md$")
REPLY_TERM = re.compile(r"(?i)\b(?:answers?|respons\w*|repl(?:y|ies)|comments?)\b")

STRICTNESS = {level: rank for rank, level in enumerate(AI_POLICY_LEVELS)}


@dataclass(frozen=True)
class Evidence:
    level: str
    source: str
    line: int
    text: str


def classify_line(window: str, agent_doc: bool = False) -> str | None:
    """AI-policy level suggested by one line plus its continuation, or None when it is not about AI use.

    Files such as AGENTS.md are addressed to coding agents, so a contribution rule
    in them is an AI policy even when it never says "AI". Those files are mostly
    coding rules, though, so without an AI term only two shapes count: a ban on
    autonomous contributions, and "do not open a PR" limited to a class of issues.
    """
    if not AI_TERM.search(window):
        if not agent_doc:
            return None
        if AUTONOMOUS.search(window) and BAN.search(window):
            return "human-in-loop"
        if AGENT_PR_BAN.search(window) and ISSUE_CLASS.search(window):
            return "issue-restricted"
        return None
    if BAN.search(window):
        if NEWCOMER.search(window):
            return "banned-for-newcomers"
        if AUTONOMOUS.search(window):
            return "human-in-loop"
        if ISSUE_CLASS.search(window):
            return "issue-restricted"
        return "banned"
    if ISSUE_CLASS.search(window) and (agent_doc or re.search(r"(?i)\bagents?\b", window)):
        return "issue-restricted"
    if DISCLOSE.search(window):
        return "disclosure"
    return None


def forbids_ai_replies(docs: dict[str, str]) -> tuple[str, int, str] | None:
    """A rule that answers to maintainers must be the contributor's own words, not AI output."""
    for source, text in docs.items():
        for i, line in enumerate(text.splitlines()):
            if AI_TERM.search(line) and BAN.search(line) and REPLY_TERM.search(line):
                return source, i + 1, " ".join(line.split())[:200]
    return None


def classify_ai_policy(docs: dict[str, str]) -> tuple[str, list[Evidence]]:
    """Overall level (strictest plausible reading) and the evidence lines behind it.

    A blanket "banned" line is downgraded to "human-in-loop" when the same
    documents also explicitly allow AI-assisted work, because such documents
    usually ban unreviewed use rather than all use. The result is still marked
    for verification.
    """
    evidence: list[Evidence] = []
    allows = False
    for source, text in docs.items():
        lines = text.splitlines()
        agent_doc = bool(AGENT_DOC.search(source))
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            window = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            if AI_TERM.search(window) and ALLOW.search(window):
                allows = True
            level = classify_line(window, agent_doc)
            if level:
                evidence.append(Evidence(level, source, i + 1, " ".join(line.split())[:220]))
    if not evidence:
        return "none", []
    levels = {e.level for e in evidence}
    if "banned" in levels and allows:
        levels.discard("banned")
        levels.add("human-in-loop")
        evidence = [replace(e, level="human-in-loop") if e.level == "banned" else e for e in evidence]
    overall = min(levels, key=lambda level: STRICTNESS[level])
    return overall, evidence


def first_match(pattern: str, docs: dict[str, str], flags: int = re.IGNORECASE) -> tuple[str, int, str] | None:
    regex = re.compile(pattern, flags)
    for source, text in docs.items():
        for i, line in enumerate(text.splitlines()):
            if regex.search(line):
                return source, i + 1, " ".join(line.split())[:200]
    return None


def detect_open_pr_limit(docs: dict[str, str]) -> tuple[str, tuple | None]:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "twenty": 20}
    pattern = (
        r"(?i)\b(?:at most|maximum of|max(?:imum)?|up to|limited to|only|have)\s+"
        r"(\d+|one|two|three|four|five|ten|twenty)\s+open\s+(?:pull requests?|PRs?)"
    )
    hit = first_match(pattern, docs)
    if not hit:
        return "none found", None
    number = re.search(pattern, hit[2])
    value = number.group(1).lower() if number else "?"
    return str(words.get(value, value)), hit


def workflow_commands(text: str) -> list[str]:
    """First line of every `run:` step in a GitHub Actions workflow."""
    commands = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^\s*-?\s*run:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in {"|", ">", "|-", ">-"} and i + 1 < len(lines):
            value = lines[i + 1].strip()
        if value:
            commands.append(value[:160])
    return commands


def conventional_share(titles: list[str]) -> float:
    if not titles:
        return 0.0
    conventional = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s")
    return sum(1 for t in titles if conventional.match(t)) / len(titles)


def link_styles(bodies: list[str]) -> dict[str, int]:
    counts = {"closing": 0, "reference": 0, "none": 0}
    for body in bodies:
        if re.search(r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*:?\s*(?:[\w.-]+/[\w.-]+)?#\d+", body):
            counts["closing"] += 1
        elif re.search(r"(?i)(?:\brefs?\b|part of|related:?|see)\s*:?\s*#\d+|#\d+", body):
            counts["reference"] += 1
        else:
            counts["none"] += 1
    return counts


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[index]


def disclosure_lines(bodies: list[str]) -> list[str]:
    seen: list[str] = []
    for body in bodies:
        for line in body.splitlines():
            clean = " ".join(line.split())
            if clean and AI_TERM.search(clean) and clean not in seen and not clean.startswith("<!--"):
                seen.append(clean[:160])
    return seen[:6]


def merged_sample(repo: str, size: int) -> list[dict]:
    """Recently merged PRs from outside, non-bot authors, with size stats."""
    pulls = gh_json(["api", f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100"]) or []
    picked = []
    for pr in pulls:
        user = pr.get("user") or {}
        if (
            pr.get("merged_at")
            and pr.get("author_association") in EXTERNAL_ASSOCIATIONS
            and not is_bot(user.get("login", ""), user.get("type", ""))
        ):
            picked.append(pr)
        if len(picked) >= size:
            break
    detailed = []
    for pr in picked:
        full = gh_json(["api", f"repos/{repo}/pulls/{pr['number']}"])
        if full:
            detailed.append(full)
    return detailed


def fetch_docs(repo: str, branch: str, tree: list[str]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """(policy docs, PR templates, workflow texts) fetched from raw.githubusercontent.com."""
    doc_paths = [p for p in tree if DOC_NAME.search(p) and is_policy_location(p)]
    doc_paths += [p for p in tree if CONTRIB_DOC_DIR.match(p) and CONTRIB_DOC_TOPIC.search(p.rsplit("/", 1)[-1])]
    templates = [p for p in tree if TEMPLATE_PATH.match(p)]
    workflows = [p for p in tree if WORKFLOW_PATH.match(p)][:30]
    docs = {}
    for path in dict.fromkeys(doc_paths[:25]):
        text = raw_file(repo, path, branch)
        if text:
            docs[path] = text
    template_texts = {p: t for p in templates if (t := raw_file(repo, p, branch))}
    workflow_texts = [t for p in workflows if (t := raw_file(repo, p, branch))]
    return docs, template_texts, workflow_texts


def is_policy_location(path: str) -> bool:
    """Repo-wide policy lives at the root, in .github/ or in docs/; nested AGENTS.md files only scope a directory."""
    parts = path.split("/")
    return len(parts) == 1 or (len(parts) == 2 and parts[0].lower() in {".github", "docs"})


def scoped_agent_files(tree: list[str]) -> list[str]:
    return [p for p in tree if re.search(r"(^|/)(AGENTS|CLAUDE)\.md$", p) and "/" in p][:40]


def render(repo: str, facts: dict[str, str], sections: dict[str, list[str]]) -> str:
    out = [f"# {repo}", "", f"Drafted {date.today().isoformat()} by profile_draft.py. Verify every fact.", ""]
    out += ["## Facts", ""]
    out += [f"- {key}: {value}" for key, value in facts.items()]
    for title, lines in sections.items():
        out += ["", f"## {title}", ""]
        out += lines or ["(nothing found)"]
    return "\n".join(out) + "\n"


def cite(hit: tuple | None) -> str:
    return f'{hit[0]}:{hit[1]} "{hit[2]}"' if hit else "none found"


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--stdout", action="store_true", help="print the draft instead of writing it")
    parser.add_argument("--sample", type=int, default=15, help="merged outside PRs to sample (default 15)")
    args = parser.parse_args()
    check_api_budget()

    meta = gh_json(["api", f"repos/{args.repo}"])
    if not meta:
        sys.exit(f"cannot read {args.repo}")
    branch = meta["default_branch"]
    tree_data = gh_json(["api", f"repos/{args.repo}/git/trees/{branch}?recursive=1"]) or {}
    tree = [entry["path"] for entry in tree_data.get("tree", []) if entry.get("type") == "blob"]
    if tree_data.get("truncated"):
        print("WARNING: repository tree truncated by GitHub; some documents may be missed", file=sys.stderr)

    docs, templates, workflows = fetch_docs(args.repo, branch, tree)
    corpus = {**docs, **templates}
    level, evidence = classify_ai_policy(corpus)
    limit, limit_hit = detect_open_pr_limit(corpus)
    sample = merged_sample(args.repo, args.sample)
    titles = [pr["title"] for pr in sample]
    bodies = [pr.get("body") or "" for pr in sample]
    files = [pr.get("changed_files", 0) for pr in sample]
    lines = [pr.get("additions", 0) + pr.get("deletions", 0) for pr in sample]
    styles = link_styles(bodies)

    dco = any(p.endswith("dco.yml") or p == "DCO" for p in tree) or bool(
        first_match(r"\bDCO\b|Developer Certificate of Origin|Signed-off-by|--signoff", corpus)
    )
    cla = bool(first_match(r"\bCLA\b|Contributor License Agreement|cla-assistant", corpus, 0))
    signed = bool(
        first_match(r"signed commits|commit signing|verified commits|gpg.?sign|signature verification", corpus)
    )
    release = next(
        (
            name
            for name, marker in (
                ("reno (releasenotes/notes)", "releasenotes/notes/"),
                ("newsfragment", "newsfragments/"),
                ("changeset (.changeset/)", ".changeset/"),
                ("changelog.d", "changelog.d/"),
            )
            if any(marker in p for p in tree)
        ),
        "none found",
    )
    issue_req = first_match(
        r"link(?:ed)? (?:to )?an? (?:existing |related )?issue|must (?:have|reference|link) an? issue|"
        r"associated issue|open an issue (?:first|before)|issue first",
        corpus,
    )
    replies_hit = forbids_ai_replies(corpus)
    ascii_hit = first_match(r"\bASCII\b", corpus, 0)
    ping_hit = first_match(
        r"\b\d+\s*hours?\b.*\b(review|ping|react|respon)|\b(review|ping|react|respon)\w*\b.*\b\d+\s*hours?\b", corpus
    )
    ping_number = re.search(r"(\d+)\s*hours?", ping_hit[2], re.IGNORECASE) if ping_hit else None
    ping_hours = ping_number.group(1) if ping_number else "none found"
    share = conventional_share(titles)
    template_note = ", ".join(templates) or "none found"

    facts = {
        "checked": f"{date.today().isoformat()} (auto, verify)",
        "ai_policy": f"{level} (auto, verify)",
        "disclosure_regex": "(fill in from the template and the merged sample below)",
        "link_style": f"closing {styles['closing']} / reference {styles['reference']} / none {styles['none']} "
        f"in {len(sample)} merged outside PRs (auto, verify)",
        "title_style": ("conventional" if share >= 0.6 else "free-form") + f" ({share:.0%} conventional, auto)",
        "open_pr_limit": f"{limit} (auto, verify)",
        "dco": f"{'yes' if dco else 'no'} (auto, verify)",
        "cla": f"{'yes' if cla else 'no'} (auto, verify)",
        "signed_commits": f"{'yes' if signed else 'no'} (auto, verify)",
        "ascii_only": f"{'yes' if ascii_hit else 'no'} (auto, verify)",
        "ai_review_replies": f"{'own-words-only' if replies_hit else 'allowed'} (auto, verify)",
        "release_note": f"{release} (auto, verify)",
        "issue_required": f"{'yes' if issue_req else 'no'} (auto, verify)",
        "min_ping_hours": f"{ping_hours} (auto, verify)",
        "merged_pr_files_p90": str(percentile(files, 0.9)),
        "merged_pr_lines_p90": str(percentile(lines, 0.9)),
        "pr_template": template_note,
    }

    commands = list(dict.fromkeys(c for text in workflows for c in workflow_commands(text)))
    scoped = scoped_agent_files(tree)
    sections = {
        "AI policy evidence": [f'- [{e.level}] {e.source}:{e.line} "{e.text}"' for e in evidence[:15]],
        "Other evidence": [
            f"- open PR limit: {cite(limit_hit)}",
            f"- issue requirement: {cite(issue_req)}",
            f"- ASCII rule: {cite(ascii_hit)}",
            f"- AI-written replies to maintainers: {cite(replies_hit)}",
            f"- response time: {cite(ping_hit)}",
        ],
        "Path rules": ["| glob | rule |", "|------|------|"]
        + [f"| `{p.rsplit('/', 1)[0]}/**` | read `{p}` before changing files here |" for p in scoped],
        "Local checks that match CI": [
            "Commands CI runs (first line of each step); mark the ones `make lint` or the "
            "documented local command does not cover:",
            "",
        ]
        + [f"- `{c}`" for c in commands[:40]],
        "Merged sample": [
            f"- {len(sample)} outside PRs; files median {statistics.median(files) if files else 0}, "
            f"p90 {percentile(files, 0.9)}; lines median {statistics.median(lines) if lines else 0}, "
            f"p90 {percentile(lines, 0.9)}",
            "- AI disclosure lines seen in merged bodies:",
        ]
        + [f"  - `{line}`" for line in disclosure_lines(bodies)]
        + [f"- #{pr['number']} {pr['title'][:80]}" for pr in sample[:8]],
        "How PRs die here": [
            "Run `base_rate.py` and classify the closing comments; one row per cause.",
            "",
            "| Cause | Example PR | Avoid by |",
            "|-------|------------|----------|",
        ],
        "Documents read": [f"- {p}" for p in [*docs, *templates]],
    }
    text = render(args.repo, facts, sections)

    if templates:
        cached = cache_dir() / "templates" / profile_name(args.repo)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(next(iter(templates.values())), encoding="utf-8")

    if args.stdout:
        print(text)
        return
    out_dir = data_dir() / "repos"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / profile_name(args.repo)
    if target.exists():
        target = target.with_suffix(".draft.md")
    Path(target).write_text(text, encoding="utf-8")
    print(f"draft written to {target}")
    print(f"AI policy (auto): {level}; {len(evidence)} evidence lines. Verify the Facts block before using it.")


if __name__ == "__main__":
    main()
