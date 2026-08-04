"""Check docs/glossary.md against the rules that make it worth keeping.

A glossary nothing can fail is a wish, not an artifact. Three rules turn it into
one:

  closure      every domain word used inside a definition has its own row.
               Closure is also what SIZES the glossary -- you start from the
               words you already say, close it, and it lands where it lands.
  active voice passive voice hides the actor, and in a glossary the actor is
               almost always the module being named. "The time is shown" leaves
               out who shows it.
  live paths   every `Lives` line still points at a module that exists. A
               rotted glossary is worse than no glossary, because it is
               confidently wrong.

Run it directly (`uv run python tools/check_glossary.py`) for a report;
tests/test_glossary.py asserts the report is empty except for ALLOWED.

Format it reads:

    ## Section

    ### Term

    The definition, referring to other rows as [[term]].

    - **Lives** — the role it plays (`path/to/module.py`) → where it shows
    - **Not** — the confusable neighbour, and the difference. Optional.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

SECTION = re.compile(r"^##\s+(?P<name>.+?)\s*$")
TERM = re.compile(r"^###\s+(?P<name>.+?)\s*$")
BULLET = re.compile(r"^-\s+\*\*(?P<label>Lives|Not)\*\*\s*[—-]\s*(?P<body>.*)$")
BACKTICKED = re.compile(r"`(?P<path>[^`]+)`")
MARK = re.compile(r"\[\[(?P<target>[^\]]+)\]\]")


@dataclass
class Row:
    term: str
    section: str
    definition: str
    lives: str
    contrast: str | None
    line: int


@dataclass(frozen=True)
class Finding:
    rule: str
    term: str
    line: int
    detail: str


def parse(text: str) -> list[Row]:
    """Rows in file order. Anything before the first `###` is prose and ignored,
    so the file can carry a header explaining itself to a human."""
    rows: list[Row] = []
    state = {
        "section": "",
        "term": None,
        "line": 0,
        "definition": [],
        "lives": [],
        "contrast": [],
        "collecting": None,
    }

    def flush() -> None:
        if state["term"] is None:
            return
        rows.append(
            Row(
                term=state["term"],
                section=state["section"],
                definition=" ".join(" ".join(state["definition"]).split()),
                lives=" ".join(" ".join(state["lives"]).split()),
                contrast=" ".join(" ".join(state["contrast"]).split()) or None,
                line=state["line"],
            )
        )

    def start(term: str | None, line: int) -> None:
        state["term"] = term
        state["line"] = line
        state["definition"] = []
        state["lives"] = []
        state["contrast"] = []
        state["collecting"] = "definition" if term else None

    for number, raw in enumerate(text.splitlines(), start=1):
        heading = SECTION.match(raw)
        if heading:
            flush()
            start(None, number)
            state["section"] = heading.group("name")
            continue

        naming = TERM.match(raw)
        if naming:
            flush()
            start(naming.group("name"), number)
            continue

        if state["term"] is None:
            continue

        bullet = BULLET.match(raw)
        if bullet:
            state["collecting"] = "lives" if bullet.group("label") == "Lives" else "contrast"
            state[state["collecting"]].append(bullet.group("body"))
            continue

        # A wrapped bullet is indented; an unindented line after a bullet is a
        # malformed row, and dropping it silently would hide that. Definitions
        # take every line until the first bullet, blank ones included -- the
        # whitespace normalisation in flush() folds them away.
        if state["collecting"] == "definition":
            state["definition"].append(raw)
        elif state["collecting"] in ("lives", "contrast") and raw.startswith(" "):
            state[state["collecting"]].append(raw)

    flush()
    return rows


def check_lives_paths(rows: list[Row], repo_root: Path) -> list[Finding]:
    """Every backticked path in a `Lives` line must resolve from the repo root.

    This is the check that catches the glossary going stale rather than going
    wrong: a module gets renamed, nothing else notices, and the glossary keeps
    confidently pointing at a file nobody can open.
    """
    findings: list[Finding] = []
    for row in rows:
        if not row.lives:
            findings.append(Finding("lives-missing", row.term, row.line, "no Lives line"))
            continue
        for match in BACKTICKED.finditer(row.lives):
            candidate = match.group("path")
            if not (repo_root / candidate).exists():
                findings.append(
                    Finding("lives-path", row.term, row.line, f"{candidate} does not exist")
                )
    return findings


def normalize(term: str) -> str:
    """Fold a surface form onto its row's key: lowercase, plural stripped.

    Deliberately naive, and the naivety is safe in one direction only: a fold
    that produces a key nobody defined simply fails to resolve, which is the
    honest outcome and shows up as a finding rather than as a silent pass.
    """
    folded = term.strip().lower()
    if folded.endswith("ies") and len(folded) > 4:
        return folded[:-3] + "y"
    for suffix in ("es", "s"):
        if folded.endswith(suffix) and len(folded) > len(suffix) + 2:
            return folded[: -len(suffix)]
    return folded


def check_closure(rows: list[Row]) -> list[Finding]:
    """Both halves of closure.

    `mark-unresolved` catches a reference to a term nobody defined. `unmarked-use`
    catches the opposite and far more common failure: using a defined word
    casually, as ordinary English, inside another definition. That is how two
    definitions start to disagree without anyone deciding they should, so it is
    the half with teeth.

    A row may say its own name freely -- the alternative is every definition
    referring to itself in the third person.
    """
    defined: dict[str, str] = {}
    for row in rows:
        defined[row.term.strip().lower()] = row.term
        defined[normalize(row.term)] = row.term

    findings: list[Finding] = []
    for row in rows:
        # `Lives` is a pointer, not prose, so it stays out of both halves: it
        # names modules and roles, and marking those up would say nothing.
        prose = f"{row.definition} {row.contrast or ''}"

        for match in MARK.finditer(prose):
            target = match.group("target")
            if target.strip().lower() not in defined and normalize(target) not in defined:
                findings.append(
                    Finding("mark-unresolved", row.term, row.line, f"[[{target}]] has no row")
                )

        bare = MARK.sub(" ", prose)
        own = {row.term.strip().lower(), normalize(row.term)}
        for key, display in defined.items():
            if key in own:
                continue
            if re.search(rf"\b{re.escape(key)}(?:e?s)?\b", bare, re.IGNORECASE):
                findings.append(
                    Finding("unmarked-use", row.term, row.line, f"'{display}' used unmarked")
                )
    return list(dict.fromkeys(findings))


# Past participles that do not end in -ed. Not exhaustive by design: it grows
# the first time a real definition trips over a missing word, which is cheaper
# and more honest than guessing at English up front.
IRREGULAR = (
    "shown|written|held|read|built|kept|drawn|made|given|taken|seen|known|set|"
    "put|sent|lost|left|found|chosen|driven|thrown|run|bound|split|torn|worn"
)
# The trailing (?!-) keeps a hyphenated adjective out of it: "is read-only"
# describes a property, and calling it passive voice would be a finding nobody
# can act on -- the fastest way to teach someone to ignore a check.
PASSIVE = re.compile(
    rf"\b(?:is|are|was|were|be|been|being|gets|got)\b\s+(?:\w+ly\s+)?"
    rf"(?:\w+ed|{IRREGULAR})\b(?!-)",
    re.IGNORECASE,
)


def check_passive(rows: list[Row]) -> list[Finding]:
    """Passive voice hides the actor, and in a glossary the actor is the module.

    "The time is shown" leaves out who shows it; "the objective card shows the
    time" is the definition we actually wanted, and it names something you can
    open.
    """
    findings: list[Finding] = []
    for row in rows:
        prose = MARK.sub(lambda m: m.group("target"), f"{row.definition} {row.contrast or ''}")
        match = PASSIVE.search(prose)
        if match:
            findings.append(
                Finding("passive-voice", row.term, row.line, f"'{match.group(0)}' — name the actor")
            )
    return findings


# (rule, term) -> why this one violation stays. Per-PAIR on purpose: an
# exemption by rule alone would quietly switch a whole check off, and an
# exemption with no reason is indistinguishable from a bug someone gave up on.
ALLOWED: dict[tuple[str, str], str] = {}


def subtract_allowed(
    findings: list[Finding], allowed: dict[tuple[str, str], str] | None = None
) -> list[Finding]:
    allowed = ALLOWED if allowed is None else allowed
    return [f for f in findings if (f.rule, f.term) not in allowed]


def run(text: str, repo_root: Path) -> list[Finding]:
    rows = parse(text)
    found = check_lives_paths(rows, repo_root) + check_closure(rows) + check_passive(rows)
    return subtract_allowed(found)


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    glossary = repo_root / "docs" / "glossary.md"
    if not glossary.exists():
        print(f"no glossary at {glossary}", file=sys.stderr)
        return 1
    text = glossary.read_text(encoding="utf-8")
    rows = parse(text)
    findings = run(text, repo_root)
    for finding in findings:
        print(f"glossary.md:{finding.line}: {finding.rule}: {finding.term}: {finding.detail}")
    print(f"{len(rows)} terms, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
