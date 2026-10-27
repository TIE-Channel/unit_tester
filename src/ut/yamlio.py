"""YAML loading with YAML 1.2 core-schema scalars and duplicate-key detection.

PyYAML implements YAML 1.1, where `yes`/`no`/`on`/`off` are booleans, `010` is
octal 8, `12:30` is the integer 750, `2024-01-01` is a date and `1e-9` is a
string. In test specs those are almost always meant otherwise (signal names,
SKILL symbols, plain numbers), so this loader follows YAML 1.2: only
`true`/`false` are booleans, integers are decimal (or 0x/0o), `1e-9` is a float
and timestamps stay strings. Duplicate keys, which PyYAML silently collapses,
are an error.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

_BOOL_TAG = "tag:yaml.org,2002:bool"
_INT_TAG = "tag:yaml.org,2002:int"
_FLOAT_TAG = "tag:yaml.org,2002:float"
_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_MERGE_TAG = "tag:yaml.org,2002:merge"


class StrictLoader(yaml.SafeLoader):
    def construct_yaml_int(self, node):
        value = self.construct_scalar(node)
        if value.startswith("0x"):
            return int(value[2:], 16)
        if value.startswith("0o"):
            return int(value[2:], 8)
        return int(value, 10)

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, _ in node.value:
                if key_node.tag == _MERGE_TAG:
                    continue  # keys merged from an anchor may be overridden
                key = self.construct_object(key_node, deep=True)
                try:
                    duplicate = key in seen
                except TypeError:
                    continue
                if duplicate:
                    raise ConstructorError(
                        "while constructing a mapping", node.start_mark,
                        f"found duplicate key {key!r}", key_node.start_mark)
                seen.add(key)
        return super().construct_mapping(node, deep)


_REPLACED_TAGS = (_BOOL_TAG, _INT_TAG, _FLOAT_TAG, _TIMESTAMP_TAG)
StrictLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag not in _REPLACED_TAGS]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictLoader.add_implicit_resolver(
    _BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
# int is registered before float, so plain digit strings resolve to int first
StrictLoader.add_implicit_resolver(
    _INT_TAG, re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), list("-+0123456789"))
StrictLoader.add_implicit_resolver(
    _FLOAT_TAG,
    re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
               r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"),
    list("-+.0123456789"))
StrictLoader.add_constructor(_INT_TAG, StrictLoader.construct_yaml_int)


def load_yaml_text(text: str) -> Any:
    return yaml.load(text, Loader=StrictLoader)


def load_yaml(path: Path) -> Any:
    return load_yaml_text(Path(path).read_text(encoding="utf-8"))
