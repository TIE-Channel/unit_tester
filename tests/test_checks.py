from pathlib import Path

import pytest

from ut.adapters.base import ErrorInfo, Outcome
from ut.canonical import Normalizer, canonicalize, dumps
from ut.checks import approx_equal, check_outcome, deep_equal, is_subset, multiset_diff
from ut.spec import Case, Spec


def make_case(tmp_path: Path, expect: dict) -> Case:
    spec = Spec(path=tmp_path / "x.test.yaml", data={
        "schema_version": 1, "target": {"language": "python", "procedure": "m:f"}, "cases": []})
    return Case(spec, {"id": "c", "expect": expect})


def failures(tmp_path, expect, outcome, sandbox=None):
    sandbox = sandbox or tmp_path / "sb"
    sandbox.mkdir(exist_ok=True)
    return check_outcome(make_case(tmp_path, expect), outcome, sandbox).failures


@pytest.mark.parametrize("a, b, equal", [
    (1, 1.0, True),
    (True, 1, False),
    (False, 0, False),
    (None, None, True),
    ([1, [2, 3]], [1, [2, 3]], True),
    ([1, 2], [2, 1], False),
    ({"a": 1}, {"a": 1, "b": 2}, False),
    ("1", 1, False),
])
def test_deep_equal(a, b, equal):
    assert deep_equal(a, b) is equal


def test_approx_is_recursive_and_bool_strict():
    assert approx_equal({"x": [0.1 + 0.2]}, {"x": [0.3]}, 1e-9, 0)
    assert not approx_equal(1.0, 1.1, 1e-3, 0)
    assert not approx_equal(True, 1.0, 1, 1)


def test_subset_and_multiset():
    assert is_subset({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}, "d": 3})
    assert is_subset([{"n": "VSS"}], [{"n": "VDD", "x": 1}, {"n": "VSS", "x": 2}])
    assert not is_subset({"a": 1}, {"a": 2})
    assert multiset_diff([1, 2, 2], [2, 1, 2]) == ([], [])
    assert multiset_diff([1, 2, 2], [2, 1, 3]) == ([2], [3])


def test_canonicalize_python_values(tmp_path):
    class Opaque:
        def __repr__(self):
            return "<Opaque>"

    value = {"t": (1, 2), "s": {"b", "a"}, "p": Path("a/b"), "f": float("nan"), "o": Opaque(), 3: b"\x01"}
    assert canonicalize(value) == {"t": [1, 2], "s": ["a", "b"], "p": "a/b", "f": {"$float": "nan"},
                                   "o": {"$repr": "<Opaque>"}, "3": {"$bytes": "01"}}


def test_normalizer_makes_sandbox_paths_portable(tmp_path):
    sandbox = tmp_path / "sandbox" / "case1"
    n = Normalizer(sandbox)
    assert n.text(f"wrote {sandbox}{'/'}out{'/'}r.txt") == "wrote <sandbox>/out/r.txt"
    assert n.text(str(sandbox / "out" / "r.txt")) == "<sandbox>/out/r.txt"
    assert n.text(f"{sandbox}0/x") == f"{sandbox}0/x"  # a sibling such as case10 is not the sandbox


def test_dumps_keeps_short_structures_on_one_line():
    assert dumps({"b": [1, 2], "a": "x"}) == '{"a": "x", "b": [1, 2]}'
    long = {"k": ["x" * 50, "y" * 50]}
    assert dumps(long).splitlines() == ["{", '  "k": [', f'    "{"x" * 50}",', f'    "{"y" * 50}"', "  ]", "}"]


def test_value_mismatch_shows_expected_and_actual(tmp_path):
    [f] = failures(tmp_path, {"returns": {"value": [1, 2]}}, Outcome(returned=[1, 3]))
    assert f.where == "returns.value"
    assert "expected: [1, 2]" in f.message and "actual:   [1, 3]" in f.message


def test_combined_return_checks_all_apply(tmp_path):
    expect = {"returns": {"type": "array", "length": 2, "unordered": ["b", "a"]}}
    assert failures(tmp_path, expect, Outcome(returned=["a", "b"])) == []
    found = failures(tmp_path, expect, Outcome(returned=["a", "c", "d"]))
    assert [f.where for f in found] == ["returns.length", "returns.unordered"]


def test_unexpected_error_is_the_only_failure(tmp_path):
    outcome = Outcome(error=ErrorInfo(type="KeyError", types=["KeyError"], message="'x'", traceback="tb"))
    [f] = failures(tmp_path, {"returns": {"value": 1}, "files": [{"path": "missing.txt"}]}, outcome)
    assert f.where == "error" and "unexpected KeyError" in f.message


def test_expected_error_type_matches_base_class(tmp_path):
    outcome = Outcome(error=ErrorInfo(type="FileNotFoundError",
                                      types=["FileNotFoundError", "OSError", "Exception"], message="nope"))
    assert failures(tmp_path, {"error": {"type": "OSError", "match": "no"}}, outcome) == []
    assert [f.where for f in failures(tmp_path, {"error": {"type": "ValueError"}}, outcome)] == ["error.type"]


def test_missing_error_is_reported(tmp_path):
    [f] = failures(tmp_path, {"error": {"type": "ValueError"}}, Outcome(returned=5))
    assert "expected ValueError, but the call returned 5" in f.message


def test_text_ignore_masks_both_sides(tmp_path):
    expect = {"stdout": {"equals": "run at 10:00\nok\n", "ignore": [r"\d\d:\d\d"]}}
    assert failures(tmp_path, expect, Outcome(stdout="run at 23:59\nok\n")) == []
    [f] = failures(tmp_path, expect, Outcome(stdout="run at 23:59\nfailed\n"))
    assert "-ok" in f.message and "+failed" in f.message


def test_file_golden_compare_and_bless(tmp_path):
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    (sandbox / "out.txt").write_text("line 1\nline 2\n", encoding="utf-8")
    case = make_case(tmp_path, {"files": [{"path": "out.txt", "golden": "golden/out.txt"}]})

    [missing] = check_outcome(case, Outcome(), sandbox).failures
    assert "not found" in missing.message

    report = check_outcome(case, Outcome(), sandbox, bless=True)
    assert report.failures == [] and report.blessed == [tmp_path / "golden" / "out.txt"]
    assert check_outcome(case, Outcome(), sandbox).failures == []
    assert check_outcome(case, Outcome(), sandbox, bless=True).blessed == []  # unchanged

    (sandbox / "out.txt").write_text("line 1\nline two\n", encoding="utf-8")
    [diff] = check_outcome(case, Outcome(), sandbox).failures
    assert diff.where == "files[out.txt].golden" and "-line 2" in diff.message and "+line two" in diff.message


def test_json_file_compares_semantically(tmp_path):
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    (tmp_path / "g.json").write_text('{"b": [1, 2], "a": 1.0}', encoding="utf-8")
    (sandbox / "o.json").write_text('{"a": 1, "b": [1, 2]}', encoding="utf-8")
    expect = {"files": [{"path": "o.json", "compare": "json", "golden": "g.json"}]}
    assert failures(tmp_path, expect, Outcome(), sandbox) == []
