"""User data (~/.oss-pr), user config, repo profiles, and path-glob matching.

Layout of the data directory (override with the OSS_PR_HOME environment variable):

    ~/.oss-pr/config.md                 user preferences, `- key: value` lines
    ~/.oss-pr/ledger.md                 one row per target: estimate and outcome
    ~/.oss-pr/repos/<owner>__<repo>.md  repo profiles written by profile_draft.py, checked by the user
    ~/.oss-pr/cache/                    caches (contention maps, fetched templates)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = SKILL_ROOT / "examples" / "repos"

DEFAULTS: dict[str, str] = {
    "sources": "issues, campaigns, self-found",
    "ask_maintainers": "yes",
    "allow_test_only_prs": "yes",
    "per_repo_open_cap": "3",
    "per_repo_min_interval_hours": "24",
    "pause_after_silent_days": "7",
    "report_language": "English",
}

# Strictest first. "banned-for-newcomers" stops only contributors without merged PRs in the repo.
AI_POLICY_LEVELS = ("banned", "banned-for-newcomers", "issue-restricted", "human-in-loop", "disclosure", "none")

_FACT_LINE = re.compile(r"^\s*-\s*([a-z_][a-z0-9_]*)\s*:\s*(.*?)\s*$")


def data_dir() -> Path:
    return Path(os.environ.get("OSS_PR_HOME") or Path.home() / ".oss-pr")


def cache_dir() -> Path:
    return data_dir() / "cache"


def parse_facts(text: str, section: str | None = None) -> dict[str, str]:
    """Read `- key: value` lines, optionally only inside a `## <section>` block."""
    facts: dict[str, str] = {}
    inside = section is None
    for line in text.splitlines():
        if line.startswith("## "):
            inside = section is None or line[3:].strip().lower() == section.lower()
            continue
        if not inside:
            continue
        match = _FACT_LINE.match(line)
        if match:
            value = re.sub(r"\s+\(auto[^)]*\)$", "", match.group(2))
            facts[match.group(1)] = value
    return facts


def load_config() -> dict[str, str]:
    config = dict(DEFAULTS)
    path = data_dir() / "config.md"
    if path.exists():
        config.update(parse_facts(path.read_text(encoding="utf-8")))
    return config


def config_list(config: dict[str, str], key: str) -> list[str]:
    return [item.strip() for item in config.get(key, "").split(",") if item.strip()]


def config_int(config: dict[str, str], key: str) -> int:
    return int(config.get(key) or DEFAULTS[key])


def profile_name(repo: str) -> str:
    return repo.replace("/", "__") + ".md"


def find_profile(repo: str) -> Path | None:
    """The user's own profile first, then a shipped example with the repo's short name."""
    user = data_dir() / "repos" / profile_name(repo)
    if user.exists():
        return user
    example = EXAMPLES_DIR / f"{repo.split('/')[-1]}.md"
    if example.exists() and example.read_text(encoding="utf-8").startswith(f"# {repo}"):
        return example
    return None


def load_profile(repo: str) -> tuple[dict[str, str], list[tuple[str, str]], Path | None]:
    """Return (facts, path rules, path) for a repo, or empty values when no profile exists."""
    path = find_profile(repo)
    if path is None:
        return {}, [], None
    text = path.read_text(encoding="utf-8")
    return parse_facts(text, "Facts"), parse_path_rules(text), path


def parse_path_rules(text: str) -> list[tuple[str, str]]:
    """Rows of the `## Path rules` table: | glob | rule |."""
    rules: list[tuple[str, str]] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "path rules"
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 2 or set(cells[0]) <= {"-", ":", " "} or cells[0].lower() == "glob":
            continue
        rules.append((cells[0].strip("`"), cells[1]))
    return rules


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob: `**` crosses directories, `*` and `?` do not, `{a,b}` alternates
    (alternatives may contain wildcards themselves: `{src/*.py,**/test_*.py}`)."""
    return re.compile("^" + _glob_body(pattern) + "$")


def _glob_body(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "{":
            end = pattern.find("}", i)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                out.append("(?:" + "|".join(_glob_body(alt) for alt in pattern[i + 1 : end].split(",")) + ")")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def glob_match(pattern: str, path: str) -> bool:
    return bool(glob_to_regex(pattern).match(path.replace("\\", "/")))


def truthy(value: str | None) -> bool:
    """`yes`, `true`, `1`, `required`, optionally followed by a note: `yes (release notes)`."""
    words = (value or "").strip().lower().split()
    return bool(words) and words[0].rstrip(",:;") in {"yes", "true", "1", "required"}


def scope_globs(value: str | None) -> list[str]:
    """A fact that applies to some paths: `no` -> [], `yes` -> everything, else comma-separated globs."""
    text = (value or "").strip()
    if not text or text.lower().startswith(("no", "none", "false")):
        return []
    if text.lower() in {"yes", "true", "required"}:
        return ["**"]
    text = re.sub(r"(?i)^yes\s*[:(]?\s*", "", text).rstrip(")")
    return [g.strip().strip("`") for g in text.split(",") if g.strip()]
