import math

import pytest
import yaml

from ut.yamlio import load_yaml_text


@pytest.mark.parametrize("text, expected", [
    ("true", True),
    ("False", False),
    ("yes", "yes"),
    ("ON", "ON"),
    ("off", "off"),
    ("2024-01-01", "2024-01-01"),
    ("010", 10),
    ("0x1F", 31),
    ("0o17", 15),
    ("12:30", "12:30"),
    ("-7", -7),
    ("1e-9", 1e-9),
    ("1.5", 1.5),
    (".5", 0.5),
    ("-.inf", -math.inf),
    ("~", None),
    ("t", "t"),
    ("nil", "nil"),
])
def test_scalars_follow_yaml_1_2(text, expected):
    assert load_yaml_text(f"v: {text}")["v"] == expected
    assert type(load_yaml_text(f"v: {text}")["v"]) is type(expected)


def test_nan():
    assert math.isnan(load_yaml_text("v: .nan")["v"])


def test_duplicate_keys_are_rejected():
    with pytest.raises(yaml.YAMLError, match="duplicate key 'a'"):
        load_yaml_text("a: 1\nb: 2\na: 3\n")


def test_merge_keys_may_be_overridden():
    data = load_yaml_text("base: &b {x: 1, y: 2}\nderived:\n  <<: *b\n  y: 3\n")
    assert data["derived"] == {"x": 1, "y": 3}
