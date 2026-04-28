from __future__ import annotations

import json
import re
from typing import Any

from .config import OutputConfig


REGEX_FLAGS = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
}


class OutputParseError(ValueError):
    pass


def parse_output(stdout: str, output_config: OutputConfig) -> Any | None:
    if output_config.mode == "text":
        return None

    if output_config.mode == "native_json":
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise OutputParseError(f"command did not return valid JSON: {exc}") from exc

    regex_config = output_config.regex
    assert regex_config is not None
    flags = 0
    for flag_name in regex_config.flags:
        try:
            flags |= REGEX_FLAGS[flag_name.upper()]
        except KeyError as exc:
            raise OutputParseError(f"unsupported regex flag: {flag_name}") from exc

    pattern = re.compile(regex_config.pattern, flags)
    if regex_config.multiple:
        matches = [match.groupdict() for match in pattern.finditer(stdout)]
        if not matches:
            raise OutputParseError("regex output parser found no matches")
        return matches

    match = pattern.search(stdout)
    if match is None:
        raise OutputParseError("regex output parser found no matches")
    return match.groupdict()
