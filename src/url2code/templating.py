"""Optional response-shape templating for url2code.

When an endpoint declares ``output.template`` in its
YAML, the route handler runs this module's
``render_template`` against the run context and returns
the result as the response body instead of the default
``ToolResponse`` envelope. Endpoints without a template
keep the classic envelope -- so every existing
url2code service (brl, needle, outofoffice, pandoc)
works without YAML edits.

Why: some downstream surfaces need a specific
JSON shape at the response root, not the ToolResponse
wrapper around it. Redfish is the motivating example
(``{"@odata.id": ..., "PowerState": ...}`` at the root,
not nested under ``parsed_output``), but the same
mechanism handles any "shape my output for the client's
schema" need.

Substitution rules:

  - Walk the template recursively.
  - On a string leaf:
      * Whole-leaf form  ``"{path.to.value}"`` ->
        replace with the value at that path, native
        type preserved (int stays int, dict stays
        dict).
      * Embedded form ``"prefix-{x}-{y}-suffix"`` ->
        scan for every ``{path}`` token, str() each
        resolved value, splice. Result is always a
        string.
  - Dicts and lists pass through with children
    rendered recursively.
  - Other leaves (int / float / bool / None) pass
    through unchanged.

Failure mode is loud on purpose: an unknown path
raises ``TemplateRenderError`` and the route handler
surfaces it as a 500 with a structured error body.
Better than silently emitting partially-correct JSON
that a strict consumer (Redfish client, schema
validator) will fail on anyway.

The renderer is a pure function over (template,
context); the caller assembles the context. Tests
exercise it in isolation; route wiring is one call.
"""

from __future__ import annotations

import re
from typing import Any


# A token is ``{<dotted.path>}``. The path must start
# with a word char; subsequent parts are word chars or
# dots. No spaces, no array indexing in v1 -- if a
# template needs ``items[0]`` it can use a `static`
# constant or the regex_json parser can produce a
# flatter shape upstream.
_TOKEN_RE = re.compile(r"\{([A-Za-z_][\w.]*)\}")

# Strict ``{path}`` -- the WHOLE string is one token,
# nothing else. Used to detect the type-preserving
# substitution mode.
_WHOLE_LEAF_RE = re.compile(r"^\{([A-Za-z_][\w.]*)\}$")


class TemplateRenderError(ValueError):
    """Raised when a path in the template doesn't
    resolve against the context. Wrapped by the route
    handler into a 500 response that still carries
    the default ToolResponse envelope so operators
    can see what the CLI returned alongside the
    template mismatch."""


def _resolve_path(context: Any, path: str) -> Any:
    """Dotted-path lookup. Walks dicts by key and
    objects by attribute; either is fine, so the
    context can mix raw dicts (from parsed JSON) with
    pydantic models or namespaces freely.

    A path that runs off the end (the next part isn't
    a key or an attribute) raises ``TemplateRenderError``
    with the full path included for the operator-
    facing error body.
    """
    parts = path.split(".")
    cur: Any = context
    for index, part in enumerate(parts):
        if cur is None:
            raise TemplateRenderError(
                f"path '{path}' walks past None at "
                f"'{'.'.join(parts[:index]) or '<root>'}'"
            )
        if isinstance(cur, dict):
            if part not in cur:
                raise TemplateRenderError(
                    f"no such path: '{path}' "
                    f"(missing key '{part}')"
                )
            cur = cur[part]
            continue
        # Non-dict, non-None: try attribute access. This
        # covers pydantic models + simple namespaces
        # without forcing the caller to model_dump()
        # everything first.
        if hasattr(cur, part):
            cur = getattr(cur, part)
            continue
        raise TemplateRenderError(
            f"no such path: '{path}' "
            f"(cannot resolve '{part}' on "
            f"{type(cur).__name__})"
        )
    return cur


def _stringify(value: Any) -> str:
    """str() with a small accommodation for booleans
    (so ``True`` lands as ``"true"``, matching the
    convention executor.py already uses for command-
    arg substitution). Everything else gets the
    default str() repr -- including None, which
    becomes ``"None"``; embedded-mode users who
    don't want that should use the whole-leaf form
    and let the template ship a literal ``null``
    when the value is absent.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_template(template: Any, context: Any) -> Any:
    """Recursive renderer. See module docstring for
    the substitution rules. Returns a JSON-shaped
    value the caller can hand to ``json.dumps`` or
    ``JSONResponse`` directly."""
    if isinstance(template, dict):
        return {
            key: render_template(value, context)
            for key, value in template.items()
        }
    if isinstance(template, list):
        return [
            render_template(item, context)
            for item in template
        ]
    if isinstance(template, str):
        whole = _WHOLE_LEAF_RE.match(template)
        if whole is not None:
            # Type-preserving substitution. The path's
            # native value -- int, list, dict, None,
            # whatever -- replaces the leaf wholesale.
            return _resolve_path(context, whole.group(1))
        # Embedded substitution. Every {path} token in
        # the string is replaced with the stringified
        # value at that path; the result is always a
        # string.
        def _replace(match: re.Match[str]) -> str:
            return _stringify(
                _resolve_path(context, match.group(1))
            )
        return _TOKEN_RE.sub(_replace, template)
    # int / float / bool / None / etc. pass through.
    return template
