"""Guards against frontend/backend API drift.

A frontend call to a path the backend does not serve returns 404, and because
a 404 preflight is not a 2xx the browser reports it as
"blocked by CORS policy ... does not have HTTP ok status". That mislabels a
missing endpoint as a CORS bug, so it is asserted here directly instead.
"""

import re
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from app.config import TestingConfig

FRONTEND_SRC = BACKEND_DIR.parent / "frontend" / "src"

# Literal API paths in the frontend, e.g. "/api/items" or `/api/todos?${qs}`.
PATH_RE = re.compile(r"""["'`](/api/[^"'`\s]*)["'`]""")
DYNAMIC_RE = re.compile(r"\$\{[^}]*\}")


def _backend_paths(app):
    return {str(rule) for rule in app.url_map.iter_rules() if str(rule).startswith("/api")}


def _frontend_paths():
    found = set()
    for source in FRONTEND_SRC.rglob("*.js*"):
        text = source.read_text(encoding="utf-8", errors="ignore")
        for match in PATH_RE.finditer(text):
            # A ${...} fragment may expand to '', '/<id>' or '?page=2', so
            # everything from the first fragment onwards is an open suffix.
            norm = DYNAMIC_RE.sub("{x}", match.group(1))
            found.add((norm.split("{x}")[0].rstrip("/?/"), source.name))
    return found


def _is_covered(static_prefix, backend_paths):
    if not static_prefix:
        return False
    return any(
        path == static_prefix
        or path.startswith(static_prefix + "/")
        or path == static_prefix + "?"
        for path in backend_paths
    )


@pytest.fixture(scope="module")
def backend_paths():
    app = create_app(TestingConfig)
    return _backend_paths(app)


def test_frontend_has_no_unbacked_api_calls(backend_paths):
    if not FRONTEND_SRC.is_dir():
        pytest.skip(f"frontend sources not found at {FRONTEND_SRC}")

    orphans = sorted(
        f"{prefix}  ({source})"
        for prefix, source in _frontend_paths()
        if not _is_covered(prefix, backend_paths)
    )
    assert not orphans, (
        "These frontend API calls have no matching backend route, so the "
        "browser will fail the preflight and report a misleading CORS error:\n  "
        + "\n  ".join(orphans)
    )


def test_no_unbacked_api_calls_remain_known_cases(backend_paths):
    """The two endpoints that caused the reported incident stay wired up."""
    assert "/api/business/info" in backend_paths
    assert "/api/business/logo" in backend_paths
    assert "/api/auth/setup-pin" in backend_paths
