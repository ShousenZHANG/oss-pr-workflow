"""Draft a repository profile from the repo's own documents, CI automation and recent merged PRs.

    python profile_draft.py owner/repo [--stdout] [--sample 15]

Reads the PR template(s), CONTRIBUTING, AGENTS.md / CLAUDE.md, AI-policy files,
contributing guides (contributing-docs/, contribute/, docs/contributing/), every
CI workflow and the scripts they call (via raw.githubusercontent.com, no API
quota), plus a sample of recently merged PRs from outside contributors. Writes a
draft to ~/.oss-pr/repos/<owner>__<repo>.md (or <...>.draft.md when one exists).

Bots enforce much of a repository's real governance (auto-closing PRs without a
triaged issue, stale timers, "AI slop" detection), so the workflow files are read
for those rules too and quoted under "Bot rules".

Every detected fact is a heuristic and is marked "(auto, verify)". A human must
read the quoted evidence and correct the Facts block before the profile is used.
The AI-policy level errs toward the stricter reading when signals conflict.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from _config import AI_POLICY_LEVELS, cache_dir, data_dir, profile_name
from _gh import EXTERNAL_ASSOCIATIONS, check_api_budget, gh_json, is_bot, raw_file, use_utf8_stdout
from base_rate import mergers

DOC_NAME = re.compile(
    r"(?i)(^|/)(contributing|agents|claude|ai[_-]?policy|ai[_-]?usage|ai[_-]?guidelines|"
    r"pull_request_template|code_of_conduct_for_ai)\.(md|rst|txt)$"
)
CONTRIB_DOC_DIR = re.compile(
    r"(?i)^(contributing-docs|contribute|contributing|docs/contribut\w*|\.github/contributing)/"
)
CONTRIB_DOC_TOPIC = re.compile(
    r"(?i)(pull.?request|\bai\b|ai[_-]|gen.?ai|open.?pull|limit|commit|review|issue|guideline|readme|index)"
)
TEMPLATE_PATH = re.compile(r"(?i)^(\.github/|docs/)?pull_request_template(\.md|/[^/]+\.md)$")
WORKFLOW_PATH = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
WORKFLOW_SCRIPT_PATH = re.compile(r"^\.github/.+\.(js|cjs|mjs|ts|py|sh)$")

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
HUMAN_GATE = re.compile(
    r"(?i)\b(?:stop and (?:get|ask for|wait for) (?:explicit )?(?:human|user) (?:approval|review)|"
    r"requires? (?:explicit )?human (?:approval|review)|human (?:approval|review) (?:is )?required|"
    r"get explicit (?:human|user) approval)\b"
)
ISSUE_CLASS = re.compile(
    r"(?i)\b(?:good first issues?|help wanted|unsolicited|acceptance criteria|labell?ed|only (?:on|for) issues?)\b"
)
DISCLOSE = re.compile(
    r"(?i)\b(?:disclos\w*|declare|state that|indicate|generated-by|assisted-by|co-authored-by|influence level|"
    r"tick the|disclaimer|final line|trailer)\b|\badd\b[^`]*`[^`]+`"
)
TRAILER_DISCLOSURE = re.compile(r"(?i)\b(?:co-authored-by|assisted-by|generated-by)\b|\btrailer\b|commit message")
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
BOT_RULE = re.compile(
    r"(?i)(days-before-(?:pr-)?(?:stale|close)|stale-pr-(?:message|label)|close-pr-message|\bslop\b|"
    r"auto-?clos\w*|state:\s*['\"]closed['\"]|closeIssue|pulls\.update|has-closing-pr|"
    r"\bready\b.*label|label.*\bready\b|first-?time contributor|exempt-pr|"
    r"without (?:a|an) (?:linked|related) issue|missing (?:template|sections?)|duplicate)"
)
NOISE_COMMAND = re.compile(
    r"^(?:gh |curl |echo |printf |cat <<|#|if |then|else|fi\b|for |done|do\b|\{|\}|export |set |cd |"
    r"exit|sleep |mkdir |rm |mv |cp |chmod |sudo |apt|brew |git config|git fetch|git checkout)"
)

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
    coding rules, though, so without an AI term only three shapes count: a ban on
    autonomous contributions, a required human approval step, and "do not open a
    PR" limited to a class of issues.
    """
    if not AI_TERM.search(window):
        if not agent_doc:
            return None
        if (AUTONOMOUS.search(window) and BAN.search(window)) or HUMAN_GATE.search(window):
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
    if HUMAN_GATE.search(window):
        return "human-in-loop"
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
    evidence.sort(key=lambda e: STRICTNESS[e.level])
    return overall, evidence


def disclosure_location(evidence: list[Evidence]) -> str:
    """Where the disclosure goes: a commit trailer when the disclosure rules talk about trailers."""
    rules = [e for e in evidence if e.level == "disclosure"]
    if rules and all(TRAILER_DISCLOSURE.search(e.text) for e in rules):
        return "commit-trailer"
    if any(TRAILER_DISCLOSURE.search(e.text) for e in rules):
        return "body and commit-trailer"
    return "body"


def first_match(pattern: str, docs: dict[str, str], flags: int = re.IGNORECASE) -> tuple[str, int, str] | None:
    regex = re.compile(pattern, flags)
    for source, text in docs.items():
        for i, line in enumerate(text.splitlines()):
            if regex.search(line):
                return source, i + 1, " ".join(line.split())[:200]
    return None


def all_matches(regex: re.Pattern[str], docs: dict[str, str], limit: int) -> list[tuple[str, int, str]]:
    hits = []
    for source, text in docs.items():
        for i, line in enumerate(text.splitlines()):
            if regex.search(line):
                hits.append((source, i + 1, " ".join(line.split())[:180]))
                if len(hits) >= limit:
                    return hits
    return hits


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
    """First meaningful line of every `run:` step, without shell plumbing or GitHub write actions."""
    commands = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^\s*-?\s*run:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in {"|", ">", "|-", ">-"}:
            block = [ln.strip() for ln in lines[i + 1 : i + 12]]
            value = next((ln for ln in block if ln and not NOISE_COMMAND.match(ln)), "")
        if value and not NOISE_COMMAND.match(value) and not value.endswith("\\"):
            commands.append(value[:160])
    return commands


def title_style(titles: list[str], docs: dict[str, str]) -> str:
    """conventional, area-prefix (`Area: Summary`), imperative, or free-form; documents beat the sample."""
    if first_match(r"conventional commit", docs):
        return "conventional (documented)"
    if first_match(r"<\s*area\s*>\s*:|\barea\b[^.]{0,20}:\s*<?summary", docs):
        return "area-prefix `Area: Summary` (documented)"
    if first_match(r"\bimperative\b", docs):
        return "imperative (documented)"
    if not titles:
        return "unknown"
    conventional = sum(1 for t in titles if re.match(r"^[a-z]+(\([^)]*\))?!?:\s", t)) / len(titles)
    area = sum(1 for t in titles if re.match(r"^[A-Z][\w ./-]{1,40}:\s+\S", t)) / len(titles)
    if conventional >= 0.6:
        return f"conventional ({conventional:.0%} of sample)"
    if area >= 0.6:
        return f"area-prefix ({area:.0%} of sample)"
    return f"free-form ({conventional:.0%} conventional, {area:.0%} area-prefix)"


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
    """Lines in merged PR bodies that look like an AI disclosure statement, not product talk about AI."""
    seen: list[str] = []
    statement = re.compile(
        r"(?i)^(?:generated-by|assisted-by|co-authored-by|from \w+)\b|"
        r"\b(?:written|generated|created|assisted|authored) (?:with|by|using) (?:an? )?(?:AI|LLM|claude|copilot|"
        r"codex|cursor|chatgpt)|\bAI (?:assistance|assistant|tools? (?:was|were) used)"
    )
    for body in bodies:
        for line in body.splitlines():
            clean = " ".join(line.split())
            if clean and statement.search(clean) and clean not in seen and not clean.startswith("<!--"):
                seen.append(clean[:160])
    return seen[:6]


def merged_sample(repo: str, size: int, maintainers: set[str]) -> list[dict]:
    """Recently merged PRs from outside, non-bot authors who did not merge PRs themselves, with size stats."""
    pulls = gh_json(["api", f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100"]) or []
    picked = []
    for pr in pulls:
        user = pr.get("user") or {}
        login = user.get("login", "")
        if (
            pr.get("merged_at")
            and pr.get("author_association") in EXTERNAL_ASSOCIATIONS
            and not is_bot(login, user.get("type", ""))
            and login not in maintainers
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


def fetch_many(repo: str, branch: str, paths: list[str]) -> dict[str, str]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = list(pool.map(lambda p: raw_file(repo, p, branch), paths))
    return {p: t for p, t in zip(paths, texts, strict=True) if t}


def resolve_symlink_docs(repo: str, branch: str, docs: dict[str, str], tree: set[str]) -> dict[str, str]:
    """A file whose whole content is another path in the repo is a symlink: read the target instead."""
    resolved = {}
    for path, text in docs.items():
        target = text.strip()
        if "\n" not in target and target in tree and target != path:
            real = raw_file(repo, target, branch)
            resolved[f"{path} -> {target}"] = real or text
        else:
            resolved[path] = text
    return resolved


def fetch_docs(repo: str, branch: str, tree: list[str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """(policy docs, PR templates, workflow and workflow-script texts) from raw.githubusercontent.com."""
    templates = [p for p in tree if TEMPLATE_PATH.match(p)]
    doc_paths = [p for p in tree if DOC_NAME.search(p) and is_policy_location(p) and p not in templates]
    doc_paths += [
        p
        for p in tree
        if CONTRIB_DOC_DIR.match(p)
        and p.lower().endswith((".md", ".rst", ".txt", ".mdx"))
        and CONTRIB_DOC_TOPIC.search(p.rsplit("/", 1)[-1])
    ]
    workflow_paths = [p for p in tree if WORKFLOW_PATH.match(p)][:200]
    script_paths = [p for p in tree if WORKFLOW_SCRIPT_PATH.match(p) and not p.startswith(".github/workflows/")][:60]
    docs = resolve_symlink_docs(repo, branch, fetch_many(repo, branch, list(dict.fromkeys(doc_paths))[:40]), set(tree))
    template_texts = fetch_many(repo, branch, templates)
    automation = fetch_many(repo, branch, workflow_paths + script_paths)
    return docs, template_texts, automation


def is_policy_location(path: str) -> bool:
    """Repo-wide policy lives at the root, in .github/ or in docs/; nested AGENTS.md files only scope a directory."""
    parts = path.split("/")
    return len(parts) == 1 or (len(parts) == 2 and parts[0].lower() in {".github", "docs"})


def scoped_agent_files(tree: list[str]) -> list[str]:
    return [p for p in tree if re.search(r"(^|/)(AGENTS|CLAUDE)\.md$", p) and "/" in p][:40]


def stale_days(automation: dict[str, str]) -> str:
    found = []
    for key in ("days-before-pr-stale", "days-before-pr-close", "days-before-stale", "days-before-close"):
        hit = first_match(rf"{key}:\s*\d+", automation)
        if hit:
            number = re.search(r"\d+", hit[2].split(key, 1)[1])
            found.append(f"{key} {number.group(0) if number else '?'}")
    return ", ".join(found) or "none found"


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

    docs, templates, automation = fetch_docs(args.repo, branch, tree)
    corpus = {**docs, **templates}
    level, evidence = classify_ai_policy(corpus)
    limit, limit_hit = detect_open_pr_limit(corpus)
    sample = merged_sample(args.repo, args.sample, mergers(args.repo, 90))
    titles = [pr["title"] for pr in sample]
    bodies = [pr.get("body") or "" for pr in sample]
    files = [pr.get("changed_files", 0) for pr in sample]
    lines = [pr.get("additions", 0) + pr.get("deletions", 0) for pr in sample]
    styles = link_styles(bodies)
    everything = {**corpus, **automation}

    dco = any(p.endswith("dco.yml") or p == "DCO" for p in tree) or bool(
        first_match(r"\bDCO\b|Developer Certificate of Origin|Signed-off-by|--signoff", everything)
    )
    cla = bool(first_match(r"\bCLA\b|Contributor License Agreement|cla-assistant", everything, 0))
    signed = first_match(
        r"signed commits|commit signing|verified commits|gpg.?sign|signature verification|commits? must be signed",
        everything,
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
        None,
    )
    release_hit = first_match(
        r"changelog label|add to changelog|no-changelog|release[- ]notes? (?:label|checkbox|section)|\brn/", everything
    )
    release_note = release or (f"label or checkbox based: {cite(release_hit)}" if release_hit else "none found")
    issue_req = first_match(
        r"link(?:ed)? (?:to )?an? (?:existing |related )?issue|must (?:have|reference|link|include|close) an? "
        r"(?:related |open )?issue|associated issue|open an issue (?:first|before)|issue first|"
        r"(?:closes|fixes) #<|process starts with (?:filing|opening)|file an issue (?:first|before)",
        everything,
    )
    internal_hit = first_match(r"handled internally|internally (?:tracked|handled)|`internal` label", everything)
    replies_hit = forbids_ai_replies(corpus)
    ascii_hit = first_match(r"\bASCII\b", corpus, 0)
    ping_hit = first_match(
        r"\b\d+\s*hours?\b.*\b(review|ping|react|respon)|\b(review|ping|react|respon)\w*\b.*\b\d+\s*hours?\b", corpus
    )
    ping_number = re.search(r"(\d+)\s*hours?", ping_hit[2], re.IGNORECASE) if ping_hit else None
    ping_hours = ping_number.group(1) if ping_number else "none found"
    decisive = next((e for e in evidence if e.level == level), None)

    facts = {
        "checked": f"{date.today().isoformat()} (auto, verify)",
        "ai_policy": f"{level} (auto, verify)",
        "ai_policy_quote": f'"{decisive.text}" ({decisive.source}:{decisive.line})' if decisive else "none found",
        "disclosure_regex": "FILL IN from the template and the merged sample below",
        "disclosure_location": f"{disclosure_location(evidence)} (auto, verify)",
        "link_style": f"closing {styles['closing']} / reference {styles['reference']} / none {styles['none']} "
        f"in {len(sample)} merged outside PRs (auto, verify)",
        "title_style": f"{title_style(titles, corpus)} (auto, verify)",
        "open_pr_limit": f"{limit} (auto, verify)",
        "dco": f"{'yes' if dco else 'no'} (auto, verify)",
        "cla": f"{'yes' if cla else 'no'} (auto, verify)",
        "signed_commits": f"{'yes' if signed else 'no'} (auto, verify)",
        "ascii_only": f"{'yes' if ascii_hit else 'no'} (auto, verify)",
        "ai_review_replies": f"{'own-words-only' if replies_hit else 'allowed'} (auto, verify)",
        "release_note": f"{release_note} (auto, verify)",
        "issue_required": f"{'yes' if issue_req else 'no'} (auto, verify)",
        "internal_labels": f"{cite(internal_hit) if internal_hit else 'none found'} (auto, verify)",
        "stale_close": f"{stale_days(automation)} (auto, verify)",
        "min_ping_hours": f"{ping_hours} (auto, verify)",
        "merged_pr_files_p90": str(percentile(files, 0.9)),
        "merged_pr_lines_p90": str(percentile(lines, 0.9)),
    }

    commands = list(dict.fromkeys(c for text in automation.values() for c in workflow_commands(text)))
    scoped = scoped_agent_files(tree)
    bot_rules = all_matches(BOT_RULE, automation, 30)
    sections = {
        "AI policy evidence": [f'- [{e.level}] {e.source}:{e.line} "{e.text}"' for e in evidence[:15]],
        "Other evidence": [
            f"- PR template: {', '.join(templates) or 'none found'}",
            f"- open PR limit: {cite(limit_hit)}",
            f"- issue requirement: {cite(issue_req)}",
            f"- signed commits: {cite(signed)}",
            f"- ASCII rule: {cite(ascii_hit)}",
            f"- AI-written replies to maintainers: {cite(replies_hit)}",
            f"- response time: {cite(ping_hit)}",
        ],
        "Bot rules (from workflows and their scripts)": [
            "Automation closes, labels and comments on PRs; these lines decide outcomes more than the docs do.",
            "",
        ]
        + [f'- {source}:{line} "{text}"' for source, line, text in bot_rules],
        "Path rules": ["| glob | rule |", "|------|------|"]
        + [f"| `{p.rsplit('/', 1)[0]}/**` | read `{p}` before changing files here |" for p in scoped],
        "Local checks that match CI": [
            "Commands CI runs (first line of each step, shell plumbing removed). Mark the ones the documented",
            "local lint command does not cover, and drop release or deployment steps:",
            "",
        ]
        + [f"- `{c}`" for c in commands[:50]],
        "Merged sample": [
            f"- {len(sample)} PRs from outside contributors who do not merge PRs; files median "
            f"{statistics.median(files) if files else 0}, p90 {percentile(files, 0.9)}; lines median "
            f"{statistics.median(lines) if lines else 0}, p90 {percentile(lines, 0.9)}",
            "- AI disclosure statements seen in merged bodies:",
        ]
        + [f"  - `{line}`" for line in disclosure_lines(bodies)]
        + [f"- #{pr['number']} {pr['title'][:80]}" for pr in sample[:8]],
        "How PRs die here": [
            "Run `base_rate.py` and classify the closing comments; one row per cause.",
            "",
            "| Cause | Example PR | Avoid by |",
            "|-------|------------|----------|",
        ],
        "Documents read": [f"- {p}" for p in dict.fromkeys([*docs, *templates])]
        + [f"- {len(automation)} workflow and workflow-script files"],
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
