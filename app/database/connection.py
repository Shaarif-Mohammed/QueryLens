"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Database connection

Opens connections to the warehouse for the query executor. Connection settings come
from .env (locally) or from the hosting platform's secrets (deployed):

    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD   required except DB_PORT/DB_PASSWORD
    DB_SSLMODE                                         optional; "require" for hosted Postgres
    DB_CONNECT_TIMEOUT_SECONDS                         optional; default 10

Every connection is read-only in two independent ways: the database role has no
write grants, and the session is opened read-only, so each transaction starts with
BEGIN READ ONLY. A write would be refused by the server even if it got past the SQL
validator and even if the role were granted write access by mistake.

One connection is opened per query. That keeps state from leaking between questions
and suits a serverless database that may close idle connections.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import connection as PgConnection


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "metadata").is_dir():
            return parent
    raise FileNotFoundError("Could not find the project's metadata/ folder.")


load_dotenv(_find_project_root() / ".env")

REQUIRED_SETTINGS = ("DB_HOST", "DB_NAME", "DB_USER")


class DatabaseConfigError(RuntimeError):
    """A required connection setting is missing."""


def connection_settings() -> dict[str, object]:
    """psycopg2 connection keywords built from the environment. Never logged: it holds the password."""
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        raise DatabaseConfigError(f"Missing database setting(s): {', '.join(missing)}.")
    settings: dict[str, object] = {
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD", ""),
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10")),
        "application_name": "querylens",
    }
    if os.getenv("DB_SSLMODE"):
        settings["sslmode"] = os.getenv("DB_SSLMODE")
    return settings


def get_connection() -> PgConnection:
    """A new read-only connection. The caller closes it."""
    conn = psycopg2.connect(**connection_settings())
    conn.set_session(readonly=True, autocommit=False)
    return conn
