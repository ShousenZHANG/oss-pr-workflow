"""Ledger of targets: what you estimated, what happened. Lives in ~/.oss-pr/ledger.md.

    python ledger.py add --repo owner/repo --target "#123 empty list crash" --estimate 70 \
        --basis "returning 74.5% n=55; -5 hot file"
    python ledger.py update 7 --pr 13078 --stage ship
    python ledger.py update 7 --outcome closed-process --notes "duplicate of #13001"
    python ledger.py sync          # fill in outcomes of PRs that merged or closed
    python ledger.py show [--repo owner/repo]
    python ledger.py calibrate     # estimates against outcomes, once 20 targets are decided

Outcomes: open, merged, closed-process (closed for queue, duplicate, conflict, policy),
closed-merits (maintainers did not want the change), withdrawn (you closed it).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass, fields, replace
from datetime import date
from pathlib import Path

from _config import data_dir
from _gh import gh_json, use_utf8_stdout

OUTCOMES = {"open", "merged", "closed-process", "closed-merits", "withdrawn", ""}
DECIDED = {"merged", "closed-process", "closed-merits"}
CALIBRATE_AFTER = 20


@dataclass(frozen=True)
class Entry:
    id: int
    date: str
    repo: str
    target: str
    stage: str
    estimate: str
    basis: str
    pr: str
    outcome: str
    notes: str


COLUMNS = [f.name for f in fields(Entry)]


def ledger_path() -> Path:
    return data_dir() / "ledger.md"


def cell(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ").strip()


def render(entries: list[Entry]) -> str:
    lines = ["# oss-pr ledger", "", "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for e in entries:
        lines.append("| " + " | ".join(cell(v) for v in asdict(e).values()) + " |")
    return "\n".join(lines) + "\n"


def parse(text: str) -> list[Entry]:
    entries = []
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| id "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(COLUMNS) or not cells[0].isdigit():
            continue
        values = dict(zip(COLUMNS, cells, strict=True))
        values["id"] = int(values["id"])
        entries.append(Entry(**values))
    return entries


def load() -> list[Entry]:
    path = ledger_path()
    return parse(path.read_text(encoding="utf-8")) if path.exists() else []


def save(entries: list[Entry]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(entries), encoding="utf-8")


def calibration(entries: list[Entry]) -> list[str]:
    decided = [e for e in entries if e.outcome in DECIDED and e.estimate.rstrip("%").isdigit()]
    if len(decided) < CALIBRATE_AFTER:
        return [f"{len(decided)} decided targets with an estimate; calibration starts at {CALIBRATE_AFTER}"]
    out = []
    brier = 0.0
    buckets: dict[str, list[tuple[float, int]]] = {}
    for e in decided:
        p = int(e.estimate.rstrip("%")) / 100
        hit = 1 if e.outcome == "merged" else 0
        brier += (p - hit) ** 2
        low = min(int(p * 10) * 10, 90)
        buckets.setdefault(f"{low}-{low + 10}%", []).append((p, hit))
    out.append(
        f"{len(decided)} decided targets; Brier score {brier / len(decided):.3f} (0 is perfect, 0.25 is a coin flip)"
    )
    for name in sorted(buckets, key=lambda b: int(b.split("-")[0])):
        rows = buckets[name]
        predicted = sum(p for p, _ in rows) / len(rows)
        actual = sum(h for _, h in rows) / len(rows)
        flag = (
            "  <- estimates too high"
            if predicted - actual > 0.15
            else "  <- estimates too low"
            if actual - predicted > 0.15
            else ""
        )
        out.append(f"  {name:>8}: n={len(rows):>3} predicted {predicted:.0%} actual {actual:.0%}{flag}")
    process = sum(1 for e in decided if e.outcome == "closed-process")
    merits = sum(1 for e in decided if e.outcome == "closed-merits")
    out.append(f"closures: {process} for process reasons, {merits} on merits")
    return out


def pr_outcome(repo: str, pr: str) -> str | None:
    data = gh_json(["pr", "view", pr.lstrip("#"), "--repo", repo, "--json", "state"])
    if not data:
        return None
    return {"MERGED": "merged", "OPEN": "open", "CLOSED": "closed-process"}.get(data["state"])


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("--repo", required=True)
    add.add_argument("--target", required=True)
    add.add_argument("--estimate", required=True, help="merge probability in percent")
    add.add_argument("--basis", required=True, help="base rate, n, and each deduction")
    add.add_argument("--stage", default="recon")
    add.add_argument("--notes", default="")
    upd = sub.add_parser("update")
    upd.add_argument("id", type=int)
    for name in ("stage", "estimate", "basis", "pr", "outcome", "notes"):
        upd.add_argument(f"--{name}")
    show = sub.add_parser("show")
    show.add_argument("--repo")
    sub.add_parser("sync")
    sub.add_parser("calibrate")
    args = parser.parse_args()

    entries = load()
    if args.command == "add":
        entry = Entry(
            id=max((e.id for e in entries), default=0) + 1,
            date=date.today().isoformat(),
            repo=args.repo,
            target=args.target,
            stage=args.stage,
            estimate=args.estimate.rstrip("%") + "%",
            basis=args.basis,
            pr="",
            outcome="",
            notes=args.notes,
        )
        save([*entries, entry])
        print(f"added #{entry.id}")
    elif args.command == "update":
        changes = {k: v for k, v in vars(args).items() if k in COLUMNS and k != "id" and v is not None}
        if "outcome" in changes and changes["outcome"] not in OUTCOMES:
            sys.exit(f"outcome must be one of {sorted(OUTCOMES - {''})}")
        if not any(e.id == args.id for e in entries):
            sys.exit(f"no entry #{args.id}")
        save([replace(e, **changes) if e.id == args.id else e for e in entries])
        print(f"updated #{args.id}")
    elif args.command == "sync":
        updated = []
        for e in entries:
            if e.pr and e.outcome in {"", "open"}:
                outcome = pr_outcome(e.repo, e.pr)
                if outcome and outcome != e.outcome:
                    note = "closed: read the closing comment and set process or merits" if outcome != "merged" else ""
                    e = replace(e, outcome=outcome, notes=(e.notes + " " + note).strip())
                    print(f"#{e.id} {e.repo} {e.pr}: {outcome}")
            updated.append(e)
        save(updated)
    elif args.command == "show":
        rows = [e for e in entries if not args.repo or e.repo == args.repo]
        print(render(rows))
    elif args.command == "calibrate":
        print("\n".join(calibration(entries)))


if __name__ == "__main__":
    main()
