"""`ut` command line.

    ut validate [PATHS]       check specs (schema, files, regexes)
    ut list [PATHS]           show specs and cases
    ut run [PATHS]            run tests (wraps pytest; extra options pass through)
    ut bless [PATHS]          run and write current outputs to golden files
    ut new LANG PROCEDURE     create a spec skeleton at the standard location
    ut schema                 print the spec JSON Schema

Exit codes: 0 passed, 1 test failures, 2 spec/config/usage error,
3 infrastructure error (tool or licence unavailable, crashed worker).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from ut import __version__
from ut.config import KEEP_POLICIES, LANGUAGES, Config, load_config
from ut.errors import ConfigError
from ut.spec import SCHEMA_PATH, check_spec, discover

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INFRA = 3


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:  # everything after `--` goes to pytest untouched
        index = argv.index("--")
        argv, passthrough = argv[:index], argv[index + 1:]
    parser = _build_parser()
    ns, extra = parser.parse_known_args(argv)
    if extra and ns.command not in ("run", "bless"):
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    try:
        return ns.handler(ns, extra + passthrough)
    except ConfigError as exc:
        print(f"ut: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", metavar="PATH",
                        help="ut.toml to use (default: the nearest one at or above the current directory)")

    parser = argparse.ArgumentParser(prog="ut", description="Declarative unit tests for Python and SKILL procedures.",
                                     epilog="Exit codes: 0 passed, 1 test failures, 2 spec/config/usage error, "
                                            "3 infrastructure error.")
    parser.add_argument("--version", action="version", version=f"ut {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("validate", parents=[common], help="check specs against the schema and for missing files")
    p.add_argument("paths", nargs="*", help="spec files or directories (default: [ut] tests)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(handler=cmd_validate)

    p = sub.add_parser("list", parents=[common], help="list specs and their cases")
    p.add_argument("paths", nargs="*")
    p.add_argument("--lang", action="append", choices=LANGUAGES)
    p.add_argument("--tag", action="append", help="only cases with this tag (repeatable: any of them)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(handler=cmd_list)

    for name, help_text in (("run", "run tests"), ("bless", "run tests and write current outputs to golden files")):
        p = sub.add_parser(name, parents=[common], help=help_text,
                           description=f"{help_text}. Unrecognised options, and everything after `--`, "
                                       "are passed to pytest.")
        p.add_argument("paths", nargs="*", help="spec files, directories or pytest node ids")
        p.add_argument("-m", dest="markers", metavar="EXPR", help="tag expression, e.g. 'smoke and not slow'")
        p.add_argument("-k", dest="keyword", metavar="EXPR", help="name expression (procedure, case id)")
        p.add_argument("--suite", help="named suite from ut.toml")
        p.add_argument("--lang", action="append", choices=LANGUAGES, help="only this language (repeatable)")
        p.add_argument("--keep-sandbox", choices=KEEP_POLICIES, help="which case sandboxes to keep")
        p.add_argument("--allow-empty", action="store_true", help="exit 0 when nothing is selected")
        if name == "run":
            p.add_argument("--junit", metavar="FILE", help="write a JUnit XML report")
            p.add_argument("-n", "--workers", metavar="N", help="parallel workers ('auto' or a number; needs pytest-xdist)")
            p.add_argument("--bless", action="store_true", help="same as `ut bless`")
        p.set_defaults(handler=cmd_run, command_bless=(name == "bless"))

    p = sub.add_parser("new", parents=[common], help="create a spec skeleton at the standard location")
    p.add_argument("language", choices=LANGUAGES)
    p.add_argument("procedure", help="Python: package.module:function; SKILL: procedure name")
    p.add_argument("--area", help="sub-directory under tests/<language>/ (default: Python module path, or 'misc')")
    p.add_argument("--source", help="file defining the procedure, relative to the repository root")
    p.add_argument("--force", action="store_true", help="overwrite an existing spec")
    p.set_defaults(handler=cmd_new)

    p = sub.add_parser("schema", help="print the spec JSON Schema")
    p.add_argument("--path", action="store_true", help="print the schema's file path instead")
    p.set_defaults(handler=cmd_schema)

    return parser


def _targets(cfg: Config, paths: list[str]) -> list[Path] | None:
    targets = [Path(p) for p in paths] or cfg.tests
    missing = [p for p in targets if not p.exists()]
    for p in missing:
        print(f"ut: path not found: {p}", file=sys.stderr)
    return None if missing else targets


def cmd_validate(ns: argparse.Namespace, extra: list[str]) -> int:
    cfg = load_config(ns.config)
    targets = _targets(cfg, ns.paths)
    if targets is None:
        return EXIT_USAGE
    results = []
    for path in discover(targets, exclude=[cfg.out_dir]):
        spec, problems = check_spec(path)
        results.append({"spec": cfg.rel(path), "valid": not problems,
                        "cases": len(spec.cases) if spec else 0, "problems": problems})
    invalid = [r for r in results if not r["valid"]]
    if ns.json:
        print(json.dumps(results, indent=2))
    else:
        for r in invalid:
            print(f"{r['spec']}:")
            for problem in r["problems"]:
                print(f"  - {problem}")
        cases = sum(r["cases"] for r in results)
        print(f"{len(results) - len(invalid)}/{len(results)} spec(s) valid, {cases} case(s)")
    return EXIT_USAGE if invalid else EXIT_OK


def cmd_list(ns: argparse.Namespace, extra: list[str]) -> int:
    cfg = load_config(ns.config)
    targets = _targets(cfg, ns.paths)
    if targets is None:
        return EXIT_USAGE
    rows: list[dict[str, Any]] = []
    for path in discover(targets, exclude=[cfg.out_dir]):
        rel = cfg.rel(path)
        spec, problems = check_spec(path, allow_missing_golden=True)
        if spec is None:
            rows.append({"spec": rel, "valid": False, "problems": problems})
            continue
        if ns.lang and spec.language not in ns.lang:
            continue
        cases = [c for c in spec.cases if not ns.tag or set(ns.tag) & set(c.tags)]
        if not cases:
            continue
        rows.append({
            "spec": rel, "valid": True, "language": spec.language, "procedure": spec.procedure,
            "source": spec.source, "description": spec.description, "tags": spec.tags,
            "cases": [{"id": c.id, "nodeid": f"{rel}::{c.id}", "description": c.description,
                       "tags": c.tags, "skip": c.skip, "xfail": c.xfail} for c in cases],
        })
    if ns.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    for row in rows:
        if not row["valid"]:
            print(f"{row['spec']}  INVALID (see `ut validate`)")
            continue
        print(f"{row['procedure']}  [{row['language']}]  {row['spec']}")
        for c in row["cases"]:
            extra_tags = [t for t in c["tags"] if t not in row["tags"]]
            notes = [f"tags: {', '.join(extra_tags)}"] if extra_tags else []
            notes += [f"skip: {c['skip']}"] if c["skip"] else []
            notes += [f"xfail: {c['xfail']}"] if c["xfail"] else []
            print(f"  - {c['id']}" + (f"  ({'; '.join(notes)})" if notes else ""))
    valid = [r for r in rows if r["valid"]]
    print(f"{len(valid)} spec(s), {sum(len(r['cases']) for r in valid)} case(s)"
          + (f", {len(rows) - len(valid)} invalid" if len(valid) != len(rows) else ""))
    return EXIT_OK


def cmd_run(ns: argparse.Namespace, extra: list[str]) -> int:
    import pytest

    from ut.plugin import INFRA_PROPERTY

    cfg = load_config(ns.config)
    bless = ns.command_bless or getattr(ns, "bless", False)
    markers: list[str] = []
    languages = list(ns.lang or [])
    paths = list(ns.paths)
    if ns.suite:
        suite = cfg.suites.get(ns.suite)
        if suite is None:
            known = ", ".join(sorted(cfg.suites)) or "none defined"
            print(f"ut: unknown suite {ns.suite!r} (known: {known})", file=sys.stderr)
            return EXIT_USAGE
        if suite.markers:
            markers.append(suite.markers)
        languages = languages or suite.languages
        paths = paths or [str(p) for p in suite.paths]
    if not paths:
        paths = [str(p) for p in cfg.tests]
    missing = [p for p in paths if not Path(p.split("::")[0]).exists()]
    for p in missing:
        print(f"ut: path not found: {p}", file=sys.stderr)
    if missing:
        return EXIT_USAGE
    if ns.markers:
        markers.append(ns.markers)

    args = [*paths, "--rootdir", str(cfg.root)]
    if cfg.file:
        args += ["--ut-config", str(cfg.file)]
    if markers:
        args += ["-m", " and ".join(f"({m})" for m in markers)]
    if ns.keyword:
        args += ["-k", ns.keyword]
    for language in languages:
        args += ["--ut-lang", language]
    if bless:
        args.append("--ut-bless")
    if ns.keep_sandbox:
        args += ["--ut-keep-sandbox", ns.keep_sandbox]
    if getattr(ns, "junit", None):
        args += [f"--junitxml={ns.junit}", "-o", "junit_family=xunit2"]
    if getattr(ns, "workers", None):
        if importlib.util.find_spec("xdist") is None:
            print("ut: -n needs pytest-xdist (pip install 'ut-engine[parallel]')", file=sys.stderr)
            return EXIT_USAGE
        args += ["-n", ns.workers]
    if not _plugin_autoloaded():
        args += ["-p", "ut.plugin"]
    args += extra

    class Tracker:
        def __init__(self) -> None:
            self.infra: set[str] = set()

        def pytest_runtest_logreport(self, report: Any) -> None:
            if any(name == INFRA_PROPERTY for name, _ in report.user_properties):
                self.infra.add(report.nodeid)

    tracker = Tracker()
    code = int(pytest.main(args, plugins=[tracker]))
    if tracker.infra:
        return EXIT_INFRA
    if code in (0, 1):
        return code
    if code == 5:  # nothing collected or everything deselected
        if ns.allow_empty:
            return EXIT_OK
        print("ut: no tests selected (use --allow-empty if that is expected)", file=sys.stderr)
        return EXIT_USAGE
    if code in (2, 4):  # collection errors (invalid specs), interruption, usage errors
        return EXIT_USAGE
    return EXIT_INFRA


def cmd_new(ns: argparse.Namespace, extra: list[str]) -> int:
    from ut.scaffold import scaffold

    cfg = load_config(ns.config)
    try:
        path = scaffold(cfg, ns.language, ns.procedure, area=ns.area, source=ns.source, force=ns.force)
    except (ValueError, FileExistsError) as exc:
        print(f"ut: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rel = cfg.rel(path)
    print(f"created {rel}")
    print(f"next: fill in the cases and remove `skip`, then run: ut validate {rel} && ut run {rel}")
    return EXIT_OK


def cmd_schema(ns: argparse.Namespace, extra: list[str]) -> int:
    print(SCHEMA_PATH if ns.path else SCHEMA_PATH.read_text(encoding="utf-8"))
    return EXIT_OK


def _plugin_autoloaded() -> bool:
    """True when pytest will load ut.plugin through the installed entry point."""
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        return False
    try:
        entry_points = metadata.entry_points(group="pytest11")
    except TypeError:  # Python 3.9
        entry_points = metadata.entry_points().get("pytest11", [])
    return any(ep.value == "ut.plugin" for ep in entry_points)
