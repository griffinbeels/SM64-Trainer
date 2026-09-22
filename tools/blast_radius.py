"""The blast radius: which tests a change can affect, and why each was picked.

    uv run python tools/blast_radius.py            # summary for this checkout
    uv run python tools/blast_radius.py --why      # every reason, grouped
    uv run python tools/blast_radius.py --base X   # against a chosen commit

The merge check runs this set instead of the whole suite. Griffin, 2026-09-21:
"we ALREADY TESTED THE WHOLE SUITE. NO NEED TO RERUN ANY TESTS OTHER THAN THE
BLAST RADIUS FOR OUR CHANGES." The whole suite runs on GitHub (tools/full_run.py).

The diff is taken between this working tree (uncommitted and untracked files
included) and the BASELINE: the newest ancestor of HEAD whose full run on main
passed, else the merge-base with main. What a changed file selects:

  Python       pytest-testmon's rule on a recorded map: the tests that executed
               a code block no longer as it was (the baseline run's map, else
               the newest nightly one, else a local testmon database); a file
               no map has seen falls back to the tests that import it
  UI script    everything that imports it, up to the app shell; then every test
               naming one of those files, a CamelCase export, a class name one
               of them renders, or the tab (`title="Rank"`) whose page it is
  stylesheet   the rules that changed, not the file: their class names, then
               the components rendering those classes, as above. Only a global
               rule (an element selector, :root tokens, @font-face) selects the
               whole browser set
  test file    itself;  anything else: the tests and modules that name it
  last run     the tests that failed in this checkout's last run
  runner/lock  (the global inputs below) every test that starts no browser

The viewport sweeps are never picked here: each case sweeps every page at
every width, so any UI change would pick all of them. The full run covers them;
name one to run it locally.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import posixpath
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from test_lanes import BROWSER_SWEEPS, is_test_module, lane_of  # noqa: E402

UI = "src/sm64_events/ui"
SHELL = {f"{UI}/index.html", f"{UI}/app.js"}   # every page loads these; never a narrowing token
# Inputs every test passes through. A change here re-checks everything that
# starts no browser; the full run on GitHub covers the browser half.
GLOBAL_INPUTS = {
    "pyproject.toml", "uv.lock", "tests/conftest.py", "tests/skip_inventory.py",
    "tools/run_tests.py", "tools/test_lanes.py", "tools/test_resources.py",
    "tools/test_job.py", "tools/test_activity.py",
}
# This module is not one: it changes WHICH tests run, never how any test
# behaves, so its own guards (tests/test_blast_radius.py) are its radius and
# the full run on GitHub covers the rest.
# A test that globs one of these ("*.md", "*.js") scans the whole kind, so
# any file of it is in its radius. Data globs ("*.json") point at fixtures.
SCANNED_SUFFIXES = {"md", "js", "css", "html", "toml"}
SPECIFIC_CLASS_FILES = 3      # a class rendered by more files than this is shared chrome, not a clue
REFERENCE_SUFFIXES = ("js", "mjs", "css", "html", "json", "md", "toml", "txt", "csv", "yaml",
                      "yml", "ini", "bat", "png", "svg", "ico", "wav", "mp4", "db", "lock", "c",
                      "cpp", "h")
_NAMED_FILE = re.compile(r"(?:[\w.-]+/)?[\w.-]+\.(?:%s)\b" % "|".join(REFERENCE_SUFFIXES))
_WORD = re.compile(r"[A-Za-z_][\w-]*")
_TITLE = re.compile(r"""title=\\?["']([^"'\\]+)\\?["']""")
_GLOB = re.compile(r"\*\.(\w+)")
_CLASS_IN_SELECTOR = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
_ID_IN_SELECTOR = re.compile(r"#(-?[_a-zA-Z][\w-]*)")


def git(*args: str, root: Path = ROOT) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def plain_nodeid(name: str) -> str:
    """Drop xdist's `@<worker group>` suffix from a recorded test name."""
    head, sep, tail = name.rpartition("@")
    return head if sep and re.fullmatch(r"tests/\S*|browser_sweep_\d+|[a-z_]+", tail) else name


# --- the diff -------------------------------------------------------------------

def baseline(root: Path = ROOT, runs=None) -> tuple[str, str, int | None]:
    """(commit, how it was chosen, its full run's id or None). `runs` is the
    green full runs on main, newest first, as (sha, run id); None asks GitHub."""
    if runs is None:
        try:
            from full_run import GhUnavailable, green_runs_on_main
            runs = green_runs_on_main()
        except (GhUnavailable, OSError, ValueError) as error:
            runs, unread = [], str(error).splitlines()[0][:60]
        else:
            unread = None
    else:
        unread = None
    for sha, run_id in runs:
        if subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", sha, "HEAD"],
                          capture_output=True).returncode == 0:
            return sha, f"newest ancestor with a green full run, {run_id}", run_id
    base = git("merge-base", "HEAD", "main", root=root).strip()
    why = "merge-base with main: " + (f"could not list full runs ({unread})" if unread
                                     else "no ancestor of HEAD has a green full run")
    return base, why, None


def changed_files(base: str, root: Path = ROOT) -> dict[str, str]:
    """path -> 'A'dded / 'M'odified / 'D'eleted, working tree against `base`."""
    changes: dict[str, str] = {}
    for line in git("diff", "--name-status", "--no-renames", base, root=root).splitlines():
        status, _, path = line.partition("\t")
        changes[path] = status[0]
    for path in git("ls-files", "--others", "--exclude-standard", root=root).splitlines():
        changes.setdefault(path, "A")
    return changes


def old_text(base: str, path: str, root: Path = ROOT) -> str:
    try:
        return git("show", f"{base}:{path}", root=root)
    except subprocess.CalledProcessError:
        return ""


# --- what each test file refers to ----------------------------------------------

def names_in(text: str) -> set[str]:
    """File names a string mentions, each also with its parent directory
    ("settings.json" and ".claude/settings.json") so an ambiguous basename
    can be matched by its folder instead."""
    found = set()
    for match in _NAMED_FILE.findall(text):
        found.add(match.split("/")[-1])
        if "/" in match:
            found.add(match)
    return found


@dataclass
class FileRefs:
    words: set[str] = field(default_factory=set)       # identifiers and class-like words in its strings
    basenames: set[str] = field(default_factory=set)   # file names (and dir/name) its strings mention
    titles: set[str] = field(default_factory=set)      # tabs it opens: title="Rank"
    globs: set[str] = field(default_factory=set)       # suffixes it globs: "*.md" -> md
    imports: set[str] = field(default_factory=set)     # modules it imports


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            ids.add(id(body[0].value))
    return ids


def python_strings(source: str) -> list[str]:
    """Every string constant in a Python module except its docstrings."""
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except SyntaxError:
        return []
    skip = _docstrings(tree)
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip]


def js_strings(text: str) -> list[str]:
    """String and template literals of a script, comments and regular
    expressions dropped. A template's `${...}` expressions stay inside its
    text: close enough for finding class names and file names, which is all
    this is for."""
    found, i, n = [], 0, len(text)
    previous = ""   # the last code character, to tell a regex from a division
    while i < n:
        char = text[i]
        if char == "/" and not text.startswith(("//", "/*"), i) and previous in "(,=:[!&|?{};+-*%<>~^" :
            j, in_class = i + 1, False
            while j < n and text[j] != "\n" and (in_class or text[j] != "/"):
                in_class = (in_class or text[j] == "[") and text[j] != "]"
                j += 2 if text[j] == "\\" else 1
            i, previous = j + 1, "/"
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i == -1 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i == -1 else i + 2
        elif char in "'\"`":
            j = i + 1
            while j < n and text[j] != char:
                j += 2 if text[j] == "\\" else 1
            found.append(text[i + 1:j])
            i, previous = j + 1, char
        else:
            if not char.isspace():
                previous = char
            i += 1
    return found


def refs_of(source: str) -> FileRefs:
    import warnings
    refs = FileRefs()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except SyntaxError:
        return refs
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            text = node.value
            refs.words.update(_WORD.findall(text))
            refs.basenames.update(names_in(text))
            refs.titles.update(_TITLE.findall(text))
            refs.globs.update(_GLOB.findall(text))
        elif isinstance(node, ast.Import):
            refs.imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            refs.imports.add(node.module)
            refs.imports.update(f"{node.module}.{alias.name}" for alias in node.names)
    return refs


# --- the UI: modules, what imports what, what renders which class ----------------

def css_rules(text: str) -> list[tuple[str, str, str]]:
    """(at-rule context, selector, body) for every rule; @media/@container/
    @supports blocks are flattened into the context of the rules inside."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules: list[tuple[str, str, str]] = []

    def block(start: int, context: tuple[str, ...]) -> int:
        i = start
        while i < len(text):
            opening, closing = text.find("{", i), text.find("}", i)
            if closing != -1 and (opening == -1 or closing < opening):
                return closing + 1
            if opening == -1:
                return len(text)
            prelude = " ".join(text[i:opening].split())
            if re.match(r"@(media|container|supports|layer)\b", prelude):
                i = block(opening + 1, (*context, prelude))
                continue
            depth, end = 1, opening + 1
            while depth and end < len(text):
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
            rules.append((" ".join(context), prelude, " ".join(text[opening + 1:end - 1].split())))
            i = end
        return i

    block(0, ())
    return rules


def split_style(html: str) -> tuple[str, str]:
    """(the page without its <style> blocks, the style text)."""
    styles = re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S)
    return re.sub(r"<style[^>]*>.*?</style>", "<style/>", html, flags=re.S), "\n".join(styles)


class UiGraph:
    """Every UI module and page, the Vitest files that import them, and the
    classes each renders. Built from the working tree."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self.text: dict[str, str] = {}
        for path in [*(root / UI).rglob("*.js"), *(root / UI).glob("*.html"),
                     *(root / "tests" / "frontend").glob("*.test.js")]:
            if "node_modules" not in path.parts:
                self.text[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        self.imports = {path: self._imports(path, text) for path, text in self.text.items()}
        self.importers: dict[str, set[str]] = collections.defaultdict(set)
        for path, targets in self.imports.items():
            for target in targets:
                self.importers[target].add(path)
        _, style = split_style(self.text.get(f"{UI}/index.html", ""))
        self.classes = {name for _, selector, _ in css_rules(style)
                        for name in _CLASS_IN_SELECTOR.findall(selector)}
        self.strings = {path: " ".join(js_strings(text)) for path, text in self.text.items()
                        if path.endswith(".js")}
        self.renders: dict[str, set[str]] = collections.defaultdict(set)   # class -> modules
        for path, text in self.strings.items():
            if path.startswith(UI):
                for word in set(_WORD.findall(text)) & self.classes:
                    self.renders[word].add(path)
        self.surfaces = self._surfaces()

    def _imports(self, path: str, text: str) -> set[str]:
        found = set()
        specs = re.findall(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["']([^"']+)["']""", text)
        specs += re.findall(r"""<script[^>]*\bsrc=["']([^"']+)["']""", text)
        for spec in specs:
            if spec.startswith("/ui/"):
                target = f"{UI}/{spec[4:]}"
            elif spec.startswith("."):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
            else:
                continue
            if target in self.text or (self.root / target).is_file():
                found.add(target)
        return found

    def _surfaces(self) -> dict[str, set[str]]:
        """module -> the tab titles whose page it roots, read off app.js."""
        app = self.text.get(f"{UI}/app.js", "")
        names = {name: f"{UI}/{spec[2:]}" for names, spec in
                 re.findall(r"""import\s*\{([^}]*)\}\s*from\s*["'](\./[^"']+)["']""", app)
                 for name in [n.strip() for n in names.split(",")]}
        surfaces: dict[str, set[str]] = collections.defaultdict(set)
        marks = list(re.finditer(r'tab === "([^"]+)"', app))
        for index, mark in enumerate(marks):
            end = marks[index + 1].start() if index + 1 < len(marks) else len(app)
            for component in re.findall(r"<\$\{(\w+)\}", app[mark.end():end]):
                if component in names:
                    surfaces[names[component]].add(mark.group(1))
        return surfaces

    def upward(self, path: str) -> set[str]:
        """The module and everything that imports it, stopping at the shell."""
        seen, queue = {path}, [path]
        while queue:
            for importer in self.importers.get(queue.pop(), ()):
                if importer not in seen and importer not in SHELL:
                    seen.add(importer)
                    queue.append(importer)
        return seen

    def exports(self, path: str) -> set[str]:
        text = self.text.get(path, "")
        names = set(re.findall(r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|const|let|class)\s+(\w+)",
                               text, flags=re.M))
        for group in re.findall(r"^export\s*\{([^}]*)\}", text, flags=re.M):
            names.update(part.split(" as ")[-1].strip() for part in group.split(",") if part.strip())
        # CamelCase with two humps or more: one-word names ("Practice") are prose too.
        return {name for name in names if len(re.findall(r"[A-Z][a-z0-9]+", name)) >= 2}

    def tokens(self, modules: set[str]) -> tuple[set[str], set[str], set[str]]:
        """(words, basenames, titles) that name any of these modules in a test."""
        words, basenames, titles = set(), set(), set()
        for module in modules:
            basenames.add(Path(module).name)
            words |= self.exports(module)
            titles |= self.surfaces.get(module, set())
            for name in set(_WORD.findall(self.strings.get(module, ""))) & self.classes:
                if "-" in name and len(self.renders.get(name, ())) <= SPECIFIC_CLASS_FILES:
                    words.add(name)
        return words, basenames, titles


# --- the coverage map (Python) ------------------------------------------------------

# source file -> [(checksums of the code blocks a group of tests executed there, those tests)]
Coverage = dict[str, list[tuple[tuple[int, ...], set[str]]]]


def coverage_from_testmon(db: Path) -> Coverage:
    """pytest-testmon's record, read directly: per source file, each distinct
    fingerprint (the blocks a test executed there) and the tests holding it."""
    from testmon.process_code import blob_to_checksums
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select f.filename, f.method_checksums, te.test_name from test_execution te "
            "join test_execution_file_fp link on link.test_execution_id = te.id "
            "join file_fp f on f.id = link.fingerprint_id")
        grouped: dict[tuple[str, bytes], set[str]] = collections.defaultdict(set)
        for filename, checksums, test in rows:
            grouped[(filename.replace("\\", "/"), bytes(checksums or b""))].add(plain_nodeid(test))
    finally:
        con.close()
    covered: Coverage = collections.defaultdict(list)
    for (filename, blob), tests in grouped.items():
        covered[filename].append((tuple(blob_to_checksums(blob)) if blob else (), tests))
    return covered


def affected_tests(entries, source: str | None) -> set[str]:
    """pytest-testmon's own rule: a test is affected when a block it executed
    no longer exists as it was (a deleted file keeps none)."""
    from testmon.process_code import Module
    current = set(Module(source_code=source).checksums) if source is not None else set()
    return {test for checksums, tests in entries if set(checksums) - current for test in tests}


def load_coverage(root: Path = ROOT, published: Path | None = None) -> tuple[Coverage, str]:
    """The best coverage map available, and where it came from."""
    if published is not None and published.is_file():
        data = json.loads(published.read_text(encoding="utf-8"))
        tests = data["tests"]
        return ({path: [(tuple(checksums), {tests[i] for i in indexes}) for checksums, indexes in entries]
                 for path, entries in data["files"].items()},
                f"the full run's coverage map ({data.get('source', published.name)})")
    candidates = [root / ".testmondata"]
    try:
        common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", root=root).strip())
        candidates.append(common.parent / ".testmondata")
    except subprocess.CalledProcessError:
        pass
    best, best_count, best_db = {}, 0, None
    for db in dict.fromkeys(candidates):
        if not db.is_file():
            continue
        try:
            covered = coverage_from_testmon(db)
        except sqlite3.DatabaseError:
            continue
        count = len({test for entries in covered.values() for _, tests in entries for test in tests})
        if count > best_count:
            best, best_count, best_db = covered, count, db
    if best_db is None:
        return {}, "no coverage map: Python changes select the tests that import them"
    return best, f"local pytest-testmon map {best_db} ({best_count} tests)"


# --- the selection ------------------------------------------------------------------

@dataclass
class Radius:
    base: str
    base_why: str
    coverage: str
    changed: dict[str, str]
    root: Path = ROOT
    whole: dict[str, list[str]] = field(default_factory=lambda: collections.defaultdict(list))
    nodes: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    reasons: list[tuple[str, str, str, set[str]]] = field(default_factory=list)  # kind, source, what, files
    quiet: list[str] = field(default_factory=list)   # changed, yet no recorded test executed what changed
    left_to_github: list[str] = field(default_factory=list)

    def pick_files(self, kind: str, source: str, what: str, files: set[str]):
        files = {f for f in files if f not in BROWSER_SWEEPS}
        for test_file in files:
            self.whole[test_file].append(kind)
        if files:
            self.reasons.append((kind, source, what, files))

    def pick_nodes(self, kind: str, source: str, what: str, nodeids: set[str]):
        by_file = collections.defaultdict(set)
        for nodeid in nodeids:
            by_file[nodeid.split("::")[0]].add(nodeid)
        by_file = {f: ids for f, ids in by_file.items() if f not in BROWSER_SWEEPS}
        for test_file, ids in by_file.items():
            self.nodes[test_file] |= ids
        if by_file:
            self.reasons.append((kind, source, what, set(by_file)))

    def selection(self) -> dict[str, list[str] | None]:
        """test file -> None (every test in it) or the nodeids to run."""
        chosen: dict[str, list[str] | None] = {f: None for f in self.whole}
        for test_file, ids in self.nodes.items():
            if test_file not in chosen:
                chosen[test_file] = sorted(ids)
        return {f: ids for f, ids in sorted(chosen.items()) if (self.root / f).is_file()}


def _fingerprint(paths) -> tuple:
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)


_INDEXES: dict[tuple, object] = {}


def _cached(key: tuple, build):
    """Indexes rebuilt only when a file they read changed: one CLI call reads
    each once anyway, and the guard tests call `select` a dozen times."""
    if key not in _INDEXES:
        _INDEXES.clear() if len(_INDEXES) > 8 else None
        _INDEXES[key] = build()
    return _INDEXES[key]


def _test_index(root: Path) -> dict[str, FileRefs]:
    files = sorted((root / "tests").glob("test_*.py"))
    return _cached(("tests", _fingerprint(files)), lambda: {
        p.relative_to(root).as_posix(): refs_of(p.read_text(encoding="utf-8")) for p in files})


def _ui_graph(root: Path) -> "UiGraph":
    files = sorted([*(root / UI).rglob("*.js"), *(root / UI).glob("*.html"),
                    *(root / "tests" / "frontend").glob("*.test.js")])
    return _cached(("ui", _fingerprint(files)), lambda: UiGraph(root))


def select(root: Path = ROOT, base: str | None = None, runs=None,
           published: Path | None = None, *, changed: dict[str, str] | None = None,
           before=None, coverage: tuple[dict[str, set[str]], str] | None = None,
           last_failed: set[str] | None = None) -> Radius:
    """The radius of this checkout against its baseline. The keyword
    arguments stand in for git, the coverage map and pytest's cache, so the
    rules can be checked against a change nobody has to make: `changed`
    (path -> A/M/D), `before(path)` (a file's text at the base), `coverage`
    ((map, source)), `last_failed` (nodeids)."""
    if changed is None:
        base, base_why, run_id = (base, "given", None) if base else baseline(root, runs)
        changed = changed_files(base, root)
    else:
        base, base_why, run_id = base or "HEAD", "given", None
    coverage, coverage_why = coverage or _coverage_for(root, run_id, published)
    before = before or (lambda path: old_text(base, path, root))
    radius = Radius(base, base_why, coverage_why, changed, root)
    _route_changes(radius, root, changed, before, coverage)
    # A failure recorded before the baseline commit existed is superseded by
    # that commit's green full run; only a merge-base fallback keeps it.
    since = _commit_time(base, root) if run_id is not None else None
    radius.pick_nodes("failed", "", "failed in this checkout's last run",
                      _last_failed(root, since) if last_failed is None else last_failed)
    return radius


def _coverage_for(root: Path, run_id: int | None, published: Path | None):
    """The baseline's own map when its run recorded one, else the newest map
    main published (nightly), else a local pytest-testmon database."""
    if published is None:
        from full_run import GhUnavailable, coverage_map, newest_coverage_map
        try:
            published = coverage_map(run_id) if run_id is not None else None
            if published is None and (newest := newest_coverage_map()) is not None:
                published = newest[0]
        except (GhUnavailable, OSError, ValueError):
            published = None
    return load_coverage(root, published)


def _last_failed(root: Path, since: float | None = None) -> set[str]:
    """pytest's last-failed record, unless it predates `since` (the baseline
    commit's time). The primary checkout carried 80 failures from a contended
    2026-09-20 run into every merge check after main went green on GitHub,
    so an empty diff selected 70 test files."""
    path = root / ".pytest_cache" / "v" / "cache" / "lastfailed"
    try:
        if since is not None and path.stat().st_mtime < since:
            return set()
        return {plain_nodeid(n) for n in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return set()


def _commit_time(rev: str, root: Path) -> float | None:
    try:
        return float(git("show", "-s", "--format=%ct", rev, root=root).strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def _route_changes(radius: Radius, root: Path, changed: dict[str, str], before, coverage):
    """Send each changed file to the rule for its kind (the module docstring)."""
    tests = _test_index(root)
    graph: UiGraph | None = None

    def ui_graph() -> UiGraph:
        nonlocal graph
        graph = graph or _ui_graph(root)
        return graph

    def naming(words=frozenset(), basenames=frozenset(), titles=frozenset(), globs=frozenset()):
        return {name for name, refs in tests.items()
                if refs.words & words or refs.basenames & basenames
                or refs.titles & titles or refs.globs & globs}

    browser_files = {name for name in tests if lane_of(root / name) == "browser"}
    python_changes: dict[str, str] = {      # module -> the changed file that brought it in
        path: path for path in changed if path.endswith(".py") and path not in GLOBAL_INPUTS
        and not (path.startswith("tests/") and is_test_module(Path(path)))}
    sources = _SourceTexts(root)
    for path, status in sorted(changed.items()):
        suffix = Path(path).suffix.lstrip(".")
        if path in GLOBAL_INPUTS:
            radius.pick_files("global", path, path, set(tests) - browser_files)
            radius.left_to_github.append(f"{path}: the browser half runs on GitHub")
        elif path.startswith("tests/") and is_test_module(Path(path)):
            if status != "D":
                radius.pick_files("test", path, path, {path})
        elif suffix == "py":
            continue
        elif path == f"{UI}/index.html" or (path.startswith(UI) and suffix == "css"):
            _stylesheet(radius, ui_graph(), naming, browser_files, path, before, root)
        elif path.startswith((UI, "tests/frontend/")) and suffix in ("js", "mjs", "html"):
            _ui_module(radius, ui_graph(), naming, browser_files, path)
        else:
            _named_file(radius, naming, sources, path, python_changes, ui_graph)
    for path, source in python_changes.items():
        _python(radius, coverage, tests, path, source)


class _SourceTexts:
    """The file names each Python source's strings mention, read once, for
    "which module reads this file"."""

    def __init__(self, root: Path):
        self.root = root
        self._names: dict[str, set[str]] | None = None

    def naming(self, names: set[str]) -> list[str]:
        if self._names is None:
            self._names = {}
            for source in [*(self.root / "src").rglob("*.py"), *(self.root / "tools").glob("*.py")]:
                strings = python_strings(source.read_text(encoding="utf-8", errors="ignore"))
                self._names[source.relative_to(self.root).as_posix()] = {
                    found for text in strings for found in names_in(text)}
        return [path for path, mentioned in self._names.items() if mentioned & names]


def _python(radius: Radius, coverage: Coverage, tests: dict[str, FileRefs],
            path: str, source: str):
    via = "" if source == path else f" (names {Path(source).name})"
    if coverage.get(path):
        file = radius.root / path
        text = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None
        affected = affected_tests(coverage[path], text)
        radius.pick_nodes("python", source, f"{path}{via}: tests whose executed blocks changed", affected)
        if not affected:
            radius.quiet.append(path)
        return
    module = path.removesuffix(".py").replace("/", ".")
    names = {module, module.removeprefix("src."), Path(path).stem, f"tools.{Path(path).stem}"}
    importers = {name for name, refs in tests.items() if refs.imports & names}
    radius.pick_files("python", source, f"{path}{via}, not in the coverage map: its importers",
                      importers)


def _ui_module(radius, graph, naming, browser_files, path):
    if path in SHELL:
        radius.pick_files("ui", path, f"{Path(path).name} is the app shell: every page",
                          set(browser_files))
        return
    modules = graph.upward(path)
    words, basenames, titles = graph.tokens(modules)
    shown = ", ".join(sorted(Path(m).name for m in modules - {path})[:5])
    more = f" (+{len(modules) - 6})" if len(modules) > 6 else ""
    radius.pick_files("ui", path, f"{Path(path).name}" + (f", imported by {shown}{more}" if shown else ""),
                      naming(words, basenames, titles))


def _stylesheet(radius, graph, naming, browser_files, path, text_before, root):
    before_page, before = split_style(text_before(path))
    after_page, after = split_style((root / path).read_text(encoding="utf-8")
                                    if (root / path).is_file() else "")
    if path.endswith(".html") and before_page != after_page:
        radius.pick_files("ui", path, f"{Path(path).name} outside its styles: every page",
                          set(browser_files))
    old, new = collections.Counter(css_rules(before)), collections.Counter(css_rules(after))
    changed_rules = list((old - new) + (new - old))
    if not changed_rules:
        return
    names, global_rules = set(), []
    for _, selector, _body in changed_rules:
        if selector.startswith("@keyframes"):
            # A keyframe animation belongs to the classes whose rules name it.
            name = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(selector.split()[-1]))
            users = {c for _, other, body in list(old) + list(new) if name.search(body)
                     for c in _CLASS_IN_SELECTOR.findall(other)}
            (names.update(users) if 0 < len(users) <= SPECIFIC_CLASS_FILES * 3
             else global_rules.append(selector))
            continue
        parts = [part for part in selector.split(",") if part.strip()]
        if selector.startswith("@") or any(not (_CLASS_IN_SELECTOR.search(p) or _ID_IN_SELECTOR.search(p))
                                           for p in parts):
            global_rules.append(selector)
            continue
        names.update(_CLASS_IN_SELECTOR.findall(selector) + _ID_IN_SELECTOR.findall(selector))
    if global_rules:
        radius.pick_files("css", path, f"global rule {global_rules[0]!r}"
                          + (f" (+{len(global_rules) - 1})" if len(global_rules) > 1 else ""),
                          set(browser_files))
    if names:
        modules = set()
        for name in names:
            for module in graph.renders.get(name, ()):
                modules |= graph.upward(module)
        words, basenames, titles = graph.tokens(modules)
        words |= {name for name in names if "-" in name}
        shown = ", ".join(sorted(names)[:5]) + (f" (+{len(names) - 5})" if len(names) > 5 else "")
        radius.pick_files("css", path, f"rules for .{shown}", naming(words, basenames, titles))


def _file_names(root: Path, path: str) -> set[str]:
    """How code names this file: its basename when no other tracked file
    shares it, else only its parent-folder form (".claude/settings.json" is
    not the app's settings.json)."""
    name, parent = Path(path).name, Path(path).parent.name
    if not parent:
        return {name}                      # a bare name means the root one
    if parent.startswith(".") or Path(path).parts[0].startswith("."):
        return {f"{parent}/{name}"}        # agent config: never the app's file of that name
    same = [p for p in git("ls-files", "--", f"*{name}", root=root).splitlines()
            if Path(p).name == name]
    return {name} if len(same) <= 1 else {f"{parent}/{name}"}


def _named_file(radius, naming, sources, path, python_changes, ui_graph):
    names, suffix = _file_names(sources.root, path), Path(path).suffix.lstrip(".")
    shown = sorted(names)[0]
    radius.pick_files("named", path, f"{shown}, named by tests",
                      naming(basenames=names, globs={suffix} & SCANNED_SUFFIXES))
    # A module that names the file reads it: its own coverage carries the rest.
    for module in sources.naming(names):
        python_changes.setdefault(module, path)
    graph = ui_graph()
    for module, text in graph.strings.items():
        if names & names_in(text) and module not in SHELL:
            words, basenames, titles = graph.tokens(graph.upward(module))
            radius.pick_files("named", path, f"{shown}, read by {Path(module).name}",
                              naming(words, basenames, titles))


# --- output ---------------------------------------------------------------------------

def count_tests(selection: dict[str, list[str] | None], sizes: dict[str, int]) -> int:
    return sum(len(ids) if ids is not None else sizes.get(f, 0) for f, ids in selection.items())


def summary(radius: Radius) -> str:
    selection = radius.selection()
    kinds = collections.Counter(kind for kind, _, _, _ in radius.reasons)
    return (f"blast radius against {radius.base[:10]} ({radius.base_why}): "
            f"{len(radius.changed)} changed files -> {len(selection)} test files"
            + (f" [{', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))}]" if kinds else ""))


def why(radius: Radius, limit: int = 8) -> str:
    lines = [summary(radius), f"  coverage: {radius.coverage}"]
    for kind, _source, what, files in radius.reasons:
        shown = ", ".join(sorted(Path(f).name for f in files)[:limit])
        more = f" (+{len(files) - limit})" if len(files) > limit else ""
        lines.append(f"  {kind:7} {what} -> {len(files)} files: {shown}{more}")
    selecting = {source for _, source, _, _ in radius.reasons}
    unmatched = [p for p in radius.changed if p not in selecting and p not in radius.quiet]
    if radius.quiet:
        lines.append(f"  python  no recorded test executed the changed code in: {', '.join(sorted(radius.quiet)[:limit])}")
    if unmatched:
        lines.append(f"  no tests name: {', '.join(sorted(unmatched)[:limit])}"
                     + (f" (+{len(unmatched) - limit})" if len(unmatched) > limit else ""))
    for note in radius.left_to_github:
        lines.append(f"  github  {note}")
    lines.append("  github  the viewport sweeps and everything else: the full run after the push")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", help="diff against this commit instead of the baseline")
    parser.add_argument("--root", type=Path, default=ROOT, help="the checkout to read (default: this one)")
    parser.add_argument("--why", action="store_true", help="every reason, grouped")
    parser.add_argument("--write", type=Path, help="write the selection as JSON for run_tests.py")
    args = parser.parse_args(argv)
    radius = select(args.root.resolve(), args.base)
    print(why(radius) if args.why else summary(radius))
    if args.write:
        args.write.write_text(json.dumps({"files": radius.selection()}, indent=0), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
