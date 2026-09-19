"""
database.py - the only module that talks to SQLite.

Nothing here knows about Flask, prompts or embeddings. It stores rows and
returns dicts. learning.py decides what the rows mean; review.py decides who
is allowed to write them.

    init()                          create the tables, safe to call every boot
    save_analysis(...)      -> id   record a reading
    get_analysis(id)        -> dict
    save_correction(...)    -> id   record what an expert changed
    active_corrections()    -> list rows that are still teaching
    correction_for(fp)      -> dict newest correction for an identical input
    retire_correction(id)           stop a correction teaching, keep the record
    get_preference / set_preference
    stats()                 -> dict counts for the review panel
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent

DB_PATH = os.getenv("LYRIQ_DB", str(HERE / "lyriq.db"))
SCHEMA_PATH = HERE / "schema.sql"


# --- plumbing ------------------------------------------------------------

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    """Create the tables if they are not there. Idempotent."""
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(record):
    return dict(record) if record is not None else None


def _json(value, fallback=None):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def fingerprint(text: str) -> str:
    """Hash of the input with case and whitespace flattened.

    Two submissions of the same song with different indentation land on the
    same fingerprint, which is what makes "you have corrected this one before"
    work without an embedding call.
    """
    flat = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return hashlib.sha256(flat.encode("utf-8")).hexdigest()


def excerpt(text: str, limit: int = 120) -> str:
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    return flat[:limit] + ("…" if len(flat) > limit else "")


# --- analyses ------------------------------------------------------------

def save_analysis(input_text, output, source="model", learned_from=None):
    """Record one reading and return its id."""
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO analyses
                   (created_at, fingerprint, excerpt, input_text,
                    output, source, learned_from)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                fingerprint(input_text),
                excerpt(input_text),
                input_text,
                json.dumps(output, ensure_ascii=False),
                source,
                json.dumps(learned_from, ensure_ascii=False) if learned_from else None,
            ),
        )
        return cursor.lastrowid


def get_analysis(analysis_id):
    with connect() as conn:
        record = _row(conn.execute(
            "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone())
    if record is None:
        return None
    record["output"] = _json(record["output"], {})
    record["learned_from"] = _json(record["learned_from"], [])
    return record


def recent_analyses(limit=20):
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.created_at, a.excerpt, a.source,
                      (SELECT COUNT(*) FROM corrections c
                        WHERE c.analysis_id = a.id AND c.active = 1) AS corrections
                 FROM analyses a
             ORDER BY a.created_at DESC, a.id DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# --- corrections ---------------------------------------------------------

def save_correction(analysis_id, original, corrected, changed,
                    editor=None, note=None, embedding=None):
    """Record an expert edit against the analysis it corrects."""
    analysis = get_analysis(analysis_id)
    if analysis is None:
        raise ValueError(f"No analysis with id {analysis_id}.")

    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO corrections
                   (created_at, analysis_id, fingerprint, excerpt,
                    original, corrected, changed, editor, note, embedding, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                _now(),
                analysis_id,
                analysis["fingerprint"],
                analysis["excerpt"],
                json.dumps(original, ensure_ascii=False),
                json.dumps(corrected, ensure_ascii=False),
                json.dumps(changed, ensure_ascii=False),
                (editor or "").strip() or None,
                (note or "").strip() or None,
                json.dumps(embedding) if embedding else None,
            ),
        )
        return cursor.lastrowid


def _hydrate_correction(record, with_vector=False):
    record = dict(record)
    record["original"] = _json(record.get("original"), {})
    record["corrected"] = _json(record.get("corrected"), {})
    record["changed"] = _json(record.get("changed"), {})
    vector = _json(record.pop("embedding", None), None)
    if with_vector:
        record["embedding"] = vector
    record["has_embedding"] = bool(vector)
    return record


def active_corrections(with_vector=True, limit=400):
    """Every correction still allowed to teach, newest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM corrections
                WHERE active = 1
             ORDER BY created_at DESC, id DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
    return [_hydrate_correction(row, with_vector) for row in rows]


def list_corrections(limit=50, include_retired=True):
    """For the review log. Vectors stripped, they are noise on screen."""
    clause = "" if include_retired else "WHERE active = 1"
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT id, created_at, analysis_id, excerpt, changed,
                       editor, note, active
                  FROM corrections {clause}
              ORDER BY created_at DESC, id DESC
                 LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        record["changed"] = _json(record["changed"], {})
        out.append(record)
    return out


def get_correction(correction_id, with_vector=False):
    with connect() as conn:
        record = conn.execute(
            "SELECT * FROM corrections WHERE id = ?", (correction_id,)
        ).fetchone()
    return _hydrate_correction(record, with_vector) if record else None


def correction_for(text):
    """Newest active correction for an input identical to this one."""
    with connect() as conn:
        record = conn.execute(
            """SELECT * FROM corrections
                WHERE fingerprint = ? AND active = 1
             ORDER BY created_at DESC, id DESC
                LIMIT 1""",
            (fingerprint(text),),
        ).fetchone()
    return _hydrate_correction(record) if record else None


def retire_correction(correction_id, active=False):
    """Stop a correction teaching, or put it back. The row is never deleted."""
    with connect() as conn:
        conn.execute(
            "UPDATE corrections SET active = ? WHERE id = ?",
            (1 if active else 0, correction_id),
        )
    return get_correction(correction_id)


# --- preferences ---------------------------------------------------------

def get_preference(key, fallback=None):
    with connect() as conn:
        record = conn.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        ).fetchone()
    return record["value"] if record else fallback


def set_preference(key, value):
    with connect() as conn:
        conn.execute(
            """INSERT INTO preferences (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE
                  SET value = excluded.value,
                      updated_at = excluded.updated_at""",
            (key, str(value), _now()),
        )
    return get_preference(key)


# --- summary -------------------------------------------------------------

def stats():
    with connect() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM analyses)                        AS analyses,
                 (SELECT COUNT(*) FROM corrections)                     AS corrections,
                 (SELECT COUNT(*) FROM corrections WHERE active = 1)    AS teaching,
                 (SELECT COUNT(*) FROM corrections
                   WHERE active = 1 AND embedding IS NOT NULL)          AS embedded,
                 (SELECT MAX(created_at) FROM corrections)              AS last_edit"""
        ).fetchone()
    return dict(row)