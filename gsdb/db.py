"""SQLite helpers."""

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("GSDB_DB", ROOT / "data" / "gs.sqlite"))
SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA.read_text())
    return con


def reset_opera(con: sqlite3.Connection, opera_slug: str):
    """Delete all parsed data for one opera so it can be rebuilt."""
    row = con.execute("SELECT id FROM opera WHERE slug=?", (opera_slug,)).fetchone()
    if not row:
        return
    oid = row["id"]
    con.execute("""DELETE FROM voice_character WHERE voice_id IN (
        SELECT v.id FROM voice v JOIN block b ON v.block_id=b.id
        JOIN section s ON b.section_id=s.id WHERE s.opera_id=?)""", (oid,))
    con.execute("""DELETE FROM voice WHERE block_id IN (
        SELECT b.id FROM block b JOIN section s ON b.section_id=s.id
        WHERE s.opera_id=?)""", (oid,))
    con.execute("""DELETE FROM block WHERE section_id IN (
        SELECT id FROM section WHERE opera_id=?)""", (oid,))
    con.execute("DELETE FROM section WHERE opera_id=?", (oid,))
    con.execute("DELETE FROM character WHERE opera_id=?", (oid,))
    con.execute("DELETE FROM parse_report WHERE opera_id=?", (oid,))
    con.commit()
