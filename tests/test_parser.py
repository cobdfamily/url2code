"""parse_output() unit tests — covers all three modes (text,
native_json, regex) and the validation paths around each."""

from __future__ import annotations

import pytest

from url2code.config import OutputConfig, RegexOutputConfig
from url2code.parser import OutputParseError, parse_output


def _config(mode: str, **kwargs) -> OutputConfig:
    """Build an OutputConfig for ``mode`` plus optional regex
    fields, bypassing the dance of populating the Regex submodel
    by hand."""
    return OutputConfig.model_validate({"mode": mode, **kwargs})


# ---------------------------------------------------------------------------
# text — pass-through
# ---------------------------------------------------------------------------


def test_text_mode_returns_none():
    """``mode: text`` means "let the caller use raw stdout";
    parse_output returns None to signal no parsed payload."""
    assert parse_output("anything goes here\n", _config("text")) is None


# ---------------------------------------------------------------------------
# native_json
# ---------------------------------------------------------------------------


def test_native_json_decodes_object():
    out = parse_output('{"a": 1, "b": [2, 3]}', _config("native_json"))
    assert out == {"a": 1, "b": [2, 3]}


def test_native_json_decodes_array():
    out = parse_output("[1, 2, 3]", _config("native_json"))
    assert out == [1, 2, 3]


def test_native_json_invalid_raises():
    with pytest.raises(OutputParseError, match="valid JSON"):
        parse_output("not json", _config("native_json"))


# ---------------------------------------------------------------------------
# regex — single match
# ---------------------------------------------------------------------------


def test_regex_single_match_returns_groupdict():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(pattern=r"version (?P<v>\d+\.\d+)"),
    )
    out = parse_output("running tool version 1.2 finished", cfg)
    assert out == {"v": "1.2"}


def test_regex_no_match_raises():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(pattern=r"(?P<v>\d+)"),
    )
    with pytest.raises(OutputParseError, match="no matches"):
        parse_output("nothing numeric here", cfg)


# ---------------------------------------------------------------------------
# regex — multiple matches
# ---------------------------------------------------------------------------


def test_regex_multiple_returns_list_of_groupdicts():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(pattern=r"(?P<n>\d+)", multiple=True),
    )
    out = parse_output("a 1 b 2 c 3", cfg)
    assert out == [{"n": "1"}, {"n": "2"}, {"n": "3"}]


def test_regex_multiple_no_matches_raises():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(pattern=r"(?P<n>\d+)", multiple=True),
    )
    with pytest.raises(OutputParseError, match="no matches"):
        parse_output("only letters here", cfg)


# ---------------------------------------------------------------------------
# regex — flags
# ---------------------------------------------------------------------------


def test_regex_ignorecase_flag_applies():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(
            pattern=r"(?P<word>HELLO)",
            flags=["IGNORECASE"],
        ),
    )
    out = parse_output("oh hello there", cfg)
    assert out == {"word": "hello"}


def test_regex_multiline_flag_applies():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(
            pattern=r"^(?P<line>start.*)$",
            flags=["MULTILINE"],
        ),
    )
    out = parse_output("preamble\nstart of line two\nend\n", cfg)
    assert out == {"line": "start of line two"}


def test_regex_dotall_flag_applies():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(
            pattern=r"begin(?P<body>.*)end",
            flags=["DOTALL"],
        ),
    )
    out = parse_output("begin\nspans\nlines\nend", cfg)
    assert out == {"body": "\nspans\nlines\n"}


def test_regex_flag_is_case_insensitive():
    """Flag names from the YAML config can be either ``IGNORECASE``
    or ``ignorecase`` — the parser uppercases before lookup."""
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(
            pattern=r"(?P<word>foo)",
            flags=["ignorecase"],
        ),
    )
    out = parse_output("FOO and bar", cfg)
    assert out == {"word": "FOO"}


def test_regex_unsupported_flag_raises():
    cfg = _config(
        "regex_json",
        regex=RegexOutputConfig(
            pattern=r"(?P<x>.)",
            flags=["NOTAREALFLAG"],
        ),
    )
    with pytest.raises(OutputParseError, match="unsupported regex flag"):
        parse_output("a", cfg)
