from dataclasses import dataclass
import json

from app.core.text_safety import NUL_REPLACEMENT, sql_safe_json_dumps, strip_nul_data, strip_nul_text


def test_strip_nul_text_replaces_postgres_hostile_codepoint():
    assert strip_nul_text("before\x00after") == f"before{NUL_REPLACEMENT}after"


def test_strip_nul_data_recurses_and_normalises_mapping_keys():
    value = {"bad\x00key": ["a\x00b", ("c\x00d",)]}
    cleaned = strip_nul_data(value)
    assert cleaned == {f"bad{NUL_REPLACEMENT}key": [f"a{NUL_REPLACEMENT}b", [f"c{NUL_REPLACEMENT}d"]]}


def test_strip_nul_data_handles_dataclasses_and_sets_deterministically():
    @dataclass
    class Example:
        name: str
        tags: set[str]

    cleaned = strip_nul_data(Example(name="x\x00y", tags={"b\x00", "a"}))
    assert cleaned["name"] == f"x{NUL_REPLACEMENT}y"
    assert cleaned["tags"] == ["a", f"b{NUL_REPLACEMENT}"]


def test_strip_nul_data_preserves_scalar_values():
    assert strip_nul_data(42) == 42
    assert strip_nul_data(None) is None


def test_sql_safe_json_dumps_never_emits_literal_nul():
    encoded = sql_safe_json_dumps({"value": "a\x00b"})
    assert "\x00" not in encoded
    assert json.loads(encoded) == {"value": f"a{NUL_REPLACEMENT}b"}
