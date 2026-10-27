import textwrap
from pathlib import Path

import pytest

from ut.spec import check_spec, discover, load_schema

HEADER = """
schema_version: 1
target: {language: python, procedure: "pkg.mod:func"}
"""


def problems(tmp_path: Path, body: str, header: str = HEADER, **kwargs) -> list[str]:
    path = tmp_path / "x.test.yaml"
    path.write_text(textwrap.dedent(header) + textwrap.dedent(body), encoding="utf-8")
    spec, found = check_spec(path, **kwargs)
    assert (spec is None) == bool(found)
    return found


def test_schema_is_valid_draft_2020_12():
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(load_schema())


def test_minimal_spec_is_valid(tmp_path):
    assert problems(tmp_path, """
        cases:
          - id: one
            args: [1, "two", [3], {value: {k: v}}]
            expect: {returns: {value: 1}}
    """) == []


def test_extension_keys_and_anchors_are_allowed(tmp_path):
    assert problems(tmp_path, """
        x-args: &args [1, 2]
        cases:
          - id: one
            x-note: free text
            args: *args
            expect: {returns: {value: 3}}
    """) == []


@pytest.mark.parametrize("body, message", [
    ("cases: []\n", "should be non-empty"),
    ("bogus: 1\ncases: [{id: a, expect: {returns: {value: 1}}}]\n", "(top level): unknown key 'bogus' (allowed: "),
    ("cases: [{id: a, expect: {}}]\n", "should be non-empty"),
    ("cases: [{id: 'bad id', expect: {returns: {value: 1}}}]\n", "does not match"),
    ("cases: [{id: a, args: [{name: x}], expect: {returns: {value: 1}}}]\n", "must be exactly one token"),
    ("cases: [{id: a, args: [{file: /abs/path}], expect: {returns: {value: 1}}}]\n",
     "cases[0].args[0].file: '/abs/path' must be a relative path with forward slashes"),
    ("cases: [{id: a, args: [{out: x, as: list}], expect: {returns: {value: 1}}}]\n", "cases[0].args[0].as:"),
    ("cases: [{id: a, expect: {returns: {type: float}}}]\n", "is not one of"),
])
def test_schema_problems_are_located(tmp_path, body, message):
    found = problems(tmp_path, body)
    assert any(message in p for p in found), found


def test_semantic_problems(tmp_path):
    found = problems(tmp_path, """
        tags: [skip]
        cases:
          - id: a
            args: [{file: data/missing.txt}]
            expect: {returns: {value: 1}, error: {}}
          - id: a
            expect: {returns: {regex: "("}}
          - id: b
            args: [{out: ../escape.txt}]
            expect:
              files: [{path: o.bin, compare: binary, ignore: [x]}]
    """)
    text = "\n".join(found)
    for expected in ["'skip' is reserved", "input file not found: data/missing.txt",
                     "mutually exclusive", "duplicate case id", "invalid regex",
                     "must stay inside the sandbox", "'ignore' only applies to compare: text"]:
        assert expected in text, text


def test_input_name_collisions(tmp_path):
    for rel in ("a/in.txt", "b/in.txt"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    found = problems(tmp_path, """
        cases:
          - id: a
            args: [{file: a/in.txt}, {file: b/in.txt}, {out: in.txt/x}]
            expect: {returns: {value: 1}}
    """)
    assert any("would both be copied to 'in.txt'" in p for p in found)
    assert any("collides with input" in p for p in found)


def test_missing_golden_is_allowed_while_blessing(tmp_path):
    body = """
        cases:
          - id: a
            expect: {returns: {golden: golden/a.json}}
    """
    assert any("golden file not found" in p for p in problems(tmp_path, body))
    assert problems(tmp_path, body, allow_missing_golden=True) == []


def test_language_rules(tmp_path):
    python_with_stubs = problems(tmp_path, """
        stubs: {hiDisplayForm: t}
        cases: [{id: a, expect: {returns: {value: 1}}}]
    """, header="schema_version: 1\ntarget: {language: python, procedure: not_a_module_path}\n")
    assert any("expected 'package.module:function'" in p for p in python_with_stubs)
    assert any("stubs: SKILL only" in p for p in python_with_stubs)

    skill_with_mocks = problems(tmp_path, """
        mocks: {os.getcwd: {return: x}}
        cases: [{id: a, args: [{out: r.txt, as: path}], expect: {returns: {value: 1}}}]
    """, header="schema_version: 1\ntarget: {language: skill, procedure: abcFoo}\n")
    assert any("mocks: Python only" in p for p in skill_with_mocks)
    assert any("'as' is Python only" in p for p in skill_with_mocks)


def test_yaml_errors_are_reported(tmp_path):
    found = problems(tmp_path, "cases: [unclosed\n")
    assert found and found[0].startswith("YAML error")


def test_discover_skips_hidden_and_excluded_dirs(tmp_path):
    for rel in ("a/one.test.yaml", "a/.hidden/two.test.yaml", "out/three.test.yaml", "b/four.test.yml", "b/other.yaml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    found = discover([tmp_path], exclude=[tmp_path / "out"])
    assert [p.name for p in found] == ["one.test.yaml", "four.test.yml"]
