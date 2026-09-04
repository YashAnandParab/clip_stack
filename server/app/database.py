"""Persistent clip history stored in PostgreSQL."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import yaml
from psycopg.rows import dict_row


log = logging.getLogger("clipserver.database")

FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|\s*\Z)", re.DOTALL)


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


def backfill_vault_history(vault_path: Path) -> int:
    """Import older Clipstack notes that predate PostgreSQL history.

    Only notes with both ``source`` and ``clipped`` frontmatter are eligible,
    which avoids treating unrelated Markdown files in the vault as clips.
    Existing paths are left untouched, so this is safe to run at every start.
    """
    imported = 0
    for note_path in vault_path.rglob("*.md"):
        try:
            text = note_path.read_text(encoding="utf-8", errors="replace")
            match = FRONTMATTER_RE.match(text)
            if match is None:
                continue
            metadata = yaml.safe_load(match.group(1)) or {}
            if not isinstance(metadata, dict):
                continue

            source = str(metadata.get("source") or "").strip()
            clipped = metadata.get("clipped")
            if not source.startswith(("http://", "https://")) or not clipped:
                continue

            title = str(metadata.get("title") or note_path.stem).strip() or note_path.stem
            site = str(metadata.get("site") or "").strip()
            if not site:
                site = (urlparse(source).hostname or "").removeprefix("www.")

            clipped_at = clipped if isinstance(clipped, datetime) else str(clipped)
            relative_path = note_path.relative_to(vault_path).as_posix()
            body = text[match.end():]
            word_count = metadata.get("words")
            if not isinstance(word_count, int):
                word_count = len(re.findall(r"\b\w+\b", body))

            with _connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO clips (
                        url, title, site, author, published, word_count,
                        strategy, category, subcategory, path, source_client,
                        clipped_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (path) DO NOTHING
                    RETURNING id
                    """,
                    (
                        source,
                        title,
                        site,
                        str(metadata.get("author") or ""),
                        str(metadata.get("published") or ""),
                        word_count,
                        "vault import",
                        str(metadata.get("category") or ""),
                        str(metadata.get("subcategory") or ""),
                        relative_path,
                        "vault-import",
                        clipped_at,
                    ),
                )
                if cursor.fetchone() is not None:
                    imported += 1
        except (OSError, ValueError, TypeError, yaml.YAMLError, psycopg.Error) as exc:
            log.warning("could not import vault history for %s: %s", note_path, exc)
    return imported


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
