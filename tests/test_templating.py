"""Tests for the response-shape templating module.

Pure tests against `render_template`. The route-level
integration (does the YAML carry the template through?
does the handler swap to JSONResponse?) is tested in
test_route_template.py.
"""

from __future__ import annotations

import pytest

from url2code.templating import (
    TemplateRenderError,
    render_template,
)


# ---------------------------------------------------
# Whole-leaf substitution preserves native types
# ---------------------------------------------------


def test_whole_leaf_replaces_with_native_int() -> None:
    template = {"count": "{parsed_output.n}"}
    context = {"parsed_output": {"n": 42}}
    result = render_template(template, context)
    assert result == {"count": 42}
    assert isinstance(result["count"], int)


def test_whole_leaf_replaces_with_native_dict() -> None:
    """The whole-leaf form swaps in the value AT that path
    no matter its type -- dict, list, None, str, whatever.
    Lets a template hand the parser output through to a
    nested key without flattening it."""
    template = {"data": "{parsed_output.payload}"}
    context = {"parsed_output": {"payload": {"k": "v", "n": 1}}}
    result = render_template(template, context)
    assert result == {"data": {"k": "v", "n": 1}}


def test_whole_leaf_replaces_with_native_list() -> None:
    template = {"items": "{parsed_output.list}"}
    context = {"parsed_output": {"list": [1, 2, 3]}}
    assert render_template(template, context) == {"items": [1, 2, 3]}


def test_whole_leaf_replaces_with_none() -> None:
    template = {"opt": "{parsed_output.missing}"}
    context = {"parsed_output": {"missing": None}}
    assert render_template(template, context) == {"opt": None}


# ---------------------------------------------------
# Embedded substitution always produces strings
# ---------------------------------------------------


def test_embedded_substitution_is_string() -> None:
    template = {"path": "/redfish/v1/Systems/{static.id}"}
    context = {"static": {"id": "1"}}
    assert render_template(template, context) == {
        "path": "/redfish/v1/Systems/1",
    }


def test_embedded_substitution_stringifies_ints() -> None:
    template = {"label": "exit={exit_code}"}
    context = {"exit_code": 0}
    assert render_template(template, context) == {"label": "exit=0"}


def test_embedded_substitution_stringifies_booleans() -> None:
    """Booleans render lowercase to match the executor's
    convention for command-arg substitution -- so a YAML
    author can write the same {flag} token in both
    command args and response templates and get
    consistent strings."""
    template = "ok={parsed_output.ok}"
    context = {"parsed_output": {"ok": True}}
    assert render_template(template, context) == "ok=true"


def test_embedded_substitution_multiple_tokens() -> None:
    template = "/redfish/v1/Systems/{static.id}/Power/{parsed_output.rail}"
    context = {
        "static": {"id": "1"},
        "parsed_output": {"rail": "12V"},
    }
    assert render_template(template, context) == (
        "/redfish/v1/Systems/1/Power/12V"
    )


# ---------------------------------------------------
# Nested templates render recursively
# ---------------------------------------------------


def test_dict_and_list_render_recursively() -> None:
    template = {
        "@odata.id":   "/redfish/v1/Systems/{static.id}",
        "@odata.type": "#ComputerSystem.v1_0_0.ComputerSystem",
        "Id":          "{static.id}",
        "PowerState":  "{parsed_output.state}",
        "Memory":      {
            "Status": {"State": "{parsed_output.mem_state}"},
        },
        "Drives":      [
            {"Name": "{parsed_output.drive0}"},
            {"Name": "{parsed_output.drive1}"},
        ],
    }
    context = {
        "static": {"id": "1"},
        "parsed_output": {
            "state":     "On",
            "mem_state": "Enabled",
            "drive0":    "/dev/sda",
            "drive1":    "/dev/sdb",
        },
    }
    result = render_template(template, context)
    assert result["@odata.id"] == "/redfish/v1/Systems/1"
    assert result["@odata.type"] == "#ComputerSystem.v1_0_0.ComputerSystem"
    assert result["Id"] == "1"
    assert result["PowerState"] == "On"
    assert result["Memory"] == {"Status": {"State": "Enabled"}}
    assert result["Drives"] == [
        {"Name": "/dev/sda"},
        {"Name": "/dev/sdb"},
    ]


# ---------------------------------------------------
# Pass-through for non-string leaves
# ---------------------------------------------------


def test_non_string_leaves_pass_through() -> None:
    template = {
        "version": "1.0",          # bare string, no tokens
        "count":   42,
        "ratio":   0.5,
        "enabled": True,
        "extra":   None,
    }
    assert render_template(template, {}) == template


def test_string_without_tokens_passes_through() -> None:
    template = {"banner": "url2code"}
    assert render_template(template, {}) == {"banner": "url2code"}


# ---------------------------------------------------
# Error paths
# ---------------------------------------------------


def test_unknown_path_raises_with_full_path() -> None:
    template = {"x": "{parsed_output.no_such_key}"}
    context = {"parsed_output": {"a": 1}}
    with pytest.raises(TemplateRenderError) as exc_info:
        render_template(template, context)
    assert "no such path: 'parsed_output.no_such_key'" in str(exc_info.value)


def test_walks_past_none_raises_clearly() -> None:
    """When a deeper-than-the-data path is requested, the
    error names where the walk hit None so the operator
    can see which level died."""
    template = {"x": "{parsed_output.missing.deeper}"}
    context = {"parsed_output": {"missing": None}}
    with pytest.raises(TemplateRenderError) as exc_info:
        render_template(template, context)
    assert "walks past None" in str(exc_info.value)


def test_attribute_lookup_on_object() -> None:
    """Pydantic models / namespaces in the context are
    walked by attribute, not key. Lets the caller stuff a
    real ToolResponse in without dumping first. The
    whole-leaf form preserves the native int -- if the
    caller wanted a string they'd use embedded mode."""
    class Box:
        def __init__(self):
            self.value = 99
    template = "{box.value}"
    context = {"box": Box()}
    assert render_template(template, context) == 99


def test_attribute_missing_raises_with_type() -> None:
    class Box:
        pass
    template = "{box.value}"
    context = {"box": Box()}
    with pytest.raises(TemplateRenderError) as exc_info:
        render_template(template, context)
    assert "Box" in str(exc_info.value)


# ---------------------------------------------------
# Request context plumbing
# ---------------------------------------------------


def test_request_context_path_works() -> None:
    """Request-side values flow through under
    `request.<name>`. Same shape executor's command-args
    use, so a YAML author can reference the same field
    in both the CLI args AND the response template."""
    template = {
        "echo": "you sent {request.action}",
    }
    context = {"request": {"action": "reset"}}
    assert render_template(template, context) == {
        "echo": "you sent reset",
    }
