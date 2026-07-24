"""End-to-end: fixture snapshot -> build -> query DB (incl. FTS + parallel)."""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def built_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    snap = tmp / "snapshot"
    web_op = snap / "pirates" / "web_op"
    web_op.mkdir(parents=True)
    shutil.copy(FIX / "operhome_style.html", web_op / "operhome.html")
    shutil.copy(FIX / "pirates01_style.html", web_op / "pirates01.html")
    shutil.copy(FIX / "pirates05d_style.html", web_op / "pirates05d.html")
    shutil.copy(FIX / "pirates09_style.html", web_op / "pirates09.html")
    # pirates15.html deliberately missing: build must survive and report it
    dbfile = tmp / "gs.sqlite"
    env = dict(os.environ, GSDB_SNAPSHOT=str(snap), GSDB_DB=str(dbfile))
    proc = subprocess.run([sys.executable, "-m", "gsdb.build", "--opera",
                           "pirates"], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    con = sqlite3.connect(dbfile)
    con.row_factory = sqlite3.Row
    return con


def test_sections_in_order(built_db):
    rows = built_db.execute(
        "SELECT kind, number_label, act FROM section ORDER BY seq").fetchall()
    kinds = [r["kind"] for r in rows]
    assert kinds[:3] == ["number", "dialogue", "number"]
    assert rows[0]["act"] == 1
    assert rows[2]["number_label"].startswith("Nos. 9")


def test_fts_search_parallel_result(built_db):
    rows = built_db.execute(
        "SELECT v.attribution, s.number_label, "
        "  (SELECT COUNT(*) FROM voice v2 WHERE v2.block_id=v.block_id) width "
        "FROM voice_fts f JOIN voice v ON v.id=f.rowid "
        "JOIN block b ON v.block_id=b.id JOIN section s ON b.section_id=s.id "
        "WHERE voice_fts MATCH '\"did ever maiden wake\"'").fetchall()
    assert rows, "FTS found nothing"
    assert rows[0]["attribution"] == "Mabel"
    assert rows[0]["width"] >= 2, "result should sit in a parallel block"


def test_search_take_heart_is_mabel(built_db):
    rows = built_db.execute(
        "SELECT v.attribution FROM voice_fts f JOIN voice v ON v.id=f.rowid "
        "WHERE voice_fts MATCH '\"take any heart but ours\"'").fetchall()
    assert any(r["attribution"] == "Mabel" for r in rows)


def test_characters_created(built_db):
    names = {r["name"] for r in built_db.execute("SELECT name FROM character")}
    assert {"Samuel", "Mabel", "Frederic", "Kate", "Edith"} <= names
    groups = {r["name"] for r in built_db.execute(
        "SELECT name FROM character WHERE is_group=1")}
    assert "Chorus" in groups or "Girls" in groups


def test_directions_not_searchable_as_sung(built_db):
    rows = built_db.execute(
        "SELECT v.id FROM voice_fts f JOIN voice v ON v.id=f.rowid "
        "WHERE voice_fts MATCH '\"rocky sea-shore\"' "
        "AND v.text LIKE '%rocky%'").fetchall()
    assert not rows, "stage direction text leaked into searchable sung text"


def test_missing_page_reported(built_db):
    rows = built_db.execute(
        "SELECT * FROM parse_report WHERE level='error'").fetchall()
    assert any("pirates15" in r["page"] for r in rows)


def test_source_refs_present(built_db):
    rows = built_db.execute(
        "SELECT source_url, source_page FROM section").fetchall()
    for r in rows:
        assert r["source_url"].startswith("https://gsarchive.net/pirates/")
        assert r["source_page"].startswith("pirates")
