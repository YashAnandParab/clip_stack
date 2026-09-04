"""Persistent clip history stored in PostgreSQL."""

from __future__ import annotations

import os
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "clipstack"),
        user=os.environ.get("PGUSER", "clipstack"),
        password=os.environ.get("PGPASSWORD", "clipstack"),
        row_factory=dict_row,
    )


def _init_schema() -> None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS clips (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                site TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                published TEXT NOT NULL DEFAULT '',
                word_count INTEGER NOT NULL DEFAULT 0,
                strategy TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                subcategory TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL UNIQUE,
                source_client TEXT NOT NULL DEFAULT 'unknown',
                clipped_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS clips_clipped_at_idx ON clips (clipped_at DESC)"
        )


def init_database(attempts: int = 15, delay_seconds: float = 2) -> None:
    """Initialize the schema, tolerating PostgreSQL restarting beside the API."""
    last_error: psycopg.Error | None = None
    for attempt in range(attempts):
        try:
            _init_schema()
            return
        except psycopg.Error as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def database_available() -> bool:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
    except psycopg.Error:
        return False


def record_clip(values: dict[str, Any], path: str, source_client: str) -> int:
    """Insert a clip, or refresh the same path without increasing the count."""
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO clips (
                url, title, site, author, published, word_count, strategy,
                category, subcategory, path, source_client
            )
            VALUES (
                %(url)s, %(title)s, %(site)s, %(author)s, %(published)s,
                %(word_count)s, %(strategy)s, %(category)s, %(subcategory)s,
                %(path)s, %(source_client)s
            )
            ON CONFLICT (path) DO UPDATE SET
                url = EXCLUDED.url,
                title = EXCLUDED.title,
                site = EXCLUDED.site,
                author = EXCLUDED.author,
                published = EXCLUDED.published,
                word_count = EXCLUDED.word_count,
                strategy = EXCLUDED.strategy,
                category = EXCLUDED.category,
                subcategory = EXCLUDED.subcategory,
                source_client = EXCLUDED.source_client
            RETURNING id
            """,
            {
                "url": values["url"],
                "title": values["title"],
                "site": values["site"] or "",
                "author": values["author"] or "",
                "published": values["published"] or "",
                "word_count": values["word_count"] or 0,
                "strategy": values["strategy"] or "",
                "category": values["category"] or "",
                "subcategory": values["subcategory"] or "",
                "path": path,
                "source_client": source_client,
            },
        )
        row = cursor.fetchone()
        return int(row["id"])


def list_clips(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM clips")
        total = int(cursor.fetchone()["total"])
        cursor.execute(
            """
            SELECT id, url, title, site, author, published, word_count,
                   strategy, category, subcategory, path, source_client,
                   clipped_at
            FROM clips
            ORDER BY clipped_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return list(cursor.fetchall()), total


def get_clip(clip_id: int) -> dict[str, Any] | None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, path, title FROM clips WHERE id = %s",
            (clip_id,),
        )
        return cursor.fetchone()


def delete_clip_record(clip_id: int) -> bool:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM clips WHERE id = %s", (clip_id,))
        return cursor.rowcount == 1
