"""Parse untrusted JSON strictly: no repeated keys, NaN or Infinity."""

import json
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, refusing repeated keys that would hide a value."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_constant(name: str) -> Any:
    """Refuse NaN and Infinity, which strict JSON does not allow."""
    raise ValueError(f"unsupported constant {name}")


def loads_strict(text: str) -> Any:
    """Decode JSON text, raising ValueError for anything strict JSON forbids."""
    try:
        return json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_no_constant
        )
    except RecursionError:
        raise ValueError("JSON nesting is too deep") from None
