# A simple JSONC parser, written to avoid extra dependencies.
# It removes comments from JSONC strings and parses the result as JSON.
# Use internally only, so we probably don't need advance parsing features.  

import json
import re
from typing import Any


def parse(jsonc_str: str) -> Any:
    """
    Safely parse JSONC (JSON with comments) into Python objects.
    Removes // single-line and /* multi-line */ comments, ignoring those inside strings.

    Args:
        jsonc_str (str): JSONC string.

    Returns:
        Any: Parsed Python object (dict, list, etc.).

    Raises:
        ValueError: If the JSON is invalid after comment removal.
    """
    if not isinstance(jsonc_str, str):
        raise TypeError("Input must be a string containing JSONC data.")

    # Regex that matches:
    # 1. JSON strings (handles escaped quotes)
    # 2. Single-line comments
    # 3. Multi-line comments
    token_pattern = re.compile(
        r"""
        ("(?:\\.|[^"\\])*")     # 1: JSON string
        |(//[^\n\r]*)           # 2: Single-line comment
        |(/\*[\s\S]*?\*/)       # 3: Multi-line comment
        """,
        re.VERBOSE,
    )

    def _replacer(match: re.Match) -> str:
        # If group 1 matched, it's a string → keep it
        if match.group(1):
            return match.group(1)
        # If group 2 or 3 matched, it's a comment → remove it
        return ""

    # Remove comments but keep strings intact
    no_comments = token_pattern.sub(_replacer, jsonc_str).strip()

    try:
        return json.loads(no_comments)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON after removing comments: {e}")
