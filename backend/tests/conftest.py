"""Shared test fixtures for the usage-v2 test suite.

Builds a throwaway SQLite Database per test and points the codebase's
get_database() singleton (backend/app/db/database.py:401-409) at it — this repo
has no dependency-injection seam for the DB, so tests work with the existing
singleton-accessor pattern rather than fighting it: reset the module-level
_db_instance to None and set DATABASE_URL before each test, so the next
get_database() call anywhere in the app builds a fresh engine against a fresh
file. Each test gets full isolation (a distinct temp file), matching the
per-request session isolation the app relies on in production (SQLite/Postgres
via DATABASE_URL, NullPool for SQLite — see database.py:112).
"""

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import backend.app.db.database as database_module


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / f"test_{uuid.uuid4().hex}.db"
    prior_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    database_module._db_instance = None

    instance = database_module.get_database()
    yield instance

    database_module._db_instance = None
    if prior_url is not None:
        os.environ["DATABASE_URL"] = prior_url
    else:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def usage_v2_on(monkeypatch):
    monkeypatch.setenv("FEATURE_USAGE_V2", "true")
    yield


@pytest.fixture()
def usage_v2_off(monkeypatch):
    # The flag now defaults to "true" (see docs/usage-v2-migration.md) — this
    # fixture must explicitly force it off, not just unset it.
    monkeypatch.setenv("FEATURE_USAGE_V2", "false")
    yield
