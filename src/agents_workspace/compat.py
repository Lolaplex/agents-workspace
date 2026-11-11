"""Shared deprecated-env-alias helper.

Every repo reads env aliases through ``read_alias`` so the warning text and
the precedence rule stay identical (D-alias-policy: new name wins, old name
warns for one release).
"""
from __future__ import annotations

import os
import warnings
from typing import Mapping, Optional


def read_alias(env: Optional[Mapping[str, str]], new: str, old: str) -> str:
    """Return env value for ``new``; fall back to deprecated ``old`` with a warning.

    ``env=None`` reads ``os.environ``. Empty strings count as unset.
    """
    source = os.environ if env is None else env
    val = str(source.get(new) or "").strip()
    if val:
        return val
    val = str(source.get(old) or "").strip()
    if val:
        warnings.warn(
            f"{old} is deprecated; use {new} (alias removed after one release)",
            DeprecationWarning,
            stacklevel=3,
        )
        return val
    return ""
