"""Test isolation.

lodestar's tests do not delete anything from finlake's cache, but they DO
import finlake, and several of them exercise code paths that write. The
sibling repo's suite destroyed 15.5 million facts by resolving `FINLAKE_HOME`
to the real cache at import time (see finlake/conftest.py for the full
account), so the same guard is applied here rather than relying on lodestar
happening not to write today.

Set before any collection, so no test module can bind the path first.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["FINLAKE_HOME"] = tempfile.mkdtemp(prefix="lodestar_pytest_")

_REAL_HOME = Path.home() / ".finlake"

try:
    from finlake import config as _config
except Exception:      # finlake not importable here; nothing to guard
    _config = None

if _config is not None and Path(_config.DATA_DIR).resolve() == _REAL_HOME.resolve():
    raise RuntimeError(
        f"REFUSING TO RUN: the test suite resolved FINLAKE_HOME to the real "
        f"cache at {_REAL_HOME}. Something imported finlake before "
        f"conftest.py ran."
    )


def pytest_report_header(config) -> str:
    where = _config.DATA_DIR if _config is not None else "finlake not importable"
    return f"finlake cache for this run: {where}"
