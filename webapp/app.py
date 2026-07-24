"""G&S Libretto Database — web UI + JSON API.

Server-rendered FastAPI app. Read-only over data/gs.sqlite. Deployable on
Vercel (see api/index.py) or runnable locally:

    uvicorn webapp.app:app --reload
"""

import json
import os
import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("GSDB_DB", ROOT / "data" / "gs.sqlite"))

app = FastAPI(title="G&S Libretto Database", docs_url="/api/docs",
              openapi_url="/api/openapi.json")
env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                  autoescape=True)


def db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, "Database not built yet")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def render(template, **ctx):
    return HTMLResponse(env.get_template(template).render(**ctx))


def fts_escape(q: str) -> str:
    """User text -> safe FTS5 query: quoted phrase per whitespace-run,
    joined with implicit AND. '"' removed."""
    words = re.findall(r"[^\s\"']+", q)
    return " ".join(f'"{w}"' for w in words) if words else '""'


def voice_lines(row) -> list[dict]:
    return json.loads(row["lines_json"])


# ------------------------------------------------------------------ data --

def get_operas(con):
    return con.execute(
        "SELECT o.*, COUNT(DISTINCT s.id) sections FROM opera o "
        "LEFT JOIN section s ON s.opera_id=o.id "
        "GROUP BY o.id ORDER BY o.premiere_year").fetchall()


def block_payload(con, block_id: int) -> dict:
    voices = con.execute(
        "SELECT * FROM voice WHERE block_id=? ORDER BY position",
        (block_id,)).fetchall()
    b = con.execute(
        "SELECT b.*, s.number_label, s.title section_title, s.act, s.kind "
        "  section_kind, s.id section_id, s.seq section_seq, s.source_url, "
        "  o.slug opera_slug, o.title opera_title "
        "FROM block b JOIN section s ON b.section_id=s.id "
        "JOIN opera o ON s.opera_id=o.id WHERE b.id=?", (block_id,)).fetchone()
    return {
        "block_id": b["id"],
        "kind": b["kind"],
        "opera": b["opera_slug"],
        "opera_title": b["opera_title"],
        "act": b["act"],
        "number": b["number_label"],
        "number_title": b["section_title"],
        "section_id": b["section_id"],
        "section_seq": b["section_seq"],
        "source_url": b["source_url"] +
                      (f"#{b['source_anchor']}" if b["source_anchor"] else ""),
        "simultaneous": len(voices) > 1,
        "voices": [{
            "attribution": v["attribution"],
            "lines": voice_lines(v),
        } for v in voices],
    }


def search_blocks(con, q: str, opera: str | None, character: str | None,
                  limit=40) -> list[dict]:
    sql = ("SELECT DISTINCT v.block_id FROM voice_fts f "
           "JOIN voice v ON v.id=f.rowid "
           "JOIN block b ON v.block_id=b.id "
           "JOIN section s ON b.section_id=s.id "
           "JOIN opera o ON s.opera_id=o.id ")
    where, params = ["voice_fts MATCH ?"], [fts_escape(q)]
    if opera:
        where.append("o.slug=?")
        params.append(opera)
    if character:
        sql += ("JOIN voice_character vc ON vc.voice_id=v.id "
                "JOIN character c ON c.id=vc.character_id ")
        where.append("c.name LIKE ?")
        params.append(character)
    sql += "WHERE " + " AND ".join(where) + \
           " ORDER BY rank LIMIT ?"
    params.append(limit)
    ids = [r["block_id"] for r in con.execute(sql, params)]
    return [block_payload(con, i) for i in ids]


# ------------------------------------------------------------------- UI --

@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = "", opera: str = "", character: str = ""):
    if not DB_PATH.exists():
        return HTMLResponse(
            "<h1>G&amp;S Libretto Database</h1><p>The database hasn't been "
            "built yet. Run the <em>Snapshot, build &amp; validate</em> "
            "GitHub Action, which crawls politely and commits "
            "<code>data/gs.sqlite</code>; Vercel redeploys automatically.</p>")
    con = db()
    results = search_blocks(con, q, opera or None, character or None) if q else []
    return render("search.html", q=q, opera=opera, character=character,
                  operas=get_operas(con), results=results)


@app.get("/opera/{slug}", response_class=HTMLResponse)
def opera_page(slug: str):
    con = db()
    o = con.execute("SELECT * FROM opera WHERE slug=?", (slug,)).fetchone()
    if not o:
        raise HTTPException(404)
    sections = con.execute(
        "SELECT * FROM section WHERE opera_id=? ORDER BY seq",
        (o["id"],)).fetchall()
    characters = con.execute(
        "SELECT * FROM character WHERE opera_id=? ORDER BY is_group, name",
        (o["id"],)).fetchall()
    return render("opera.html", opera=o, sections=sections,
                  characters=characters)


@app.get("/opera/{slug}/{seq}", response_class=HTMLResponse)
def section_page(slug: str, seq: int):
    con = db()
    s = con.execute(
        "SELECT s.*, o.title opera_title, o.slug opera_slug FROM section s "
        "JOIN opera o ON s.opera_id=o.id WHERE o.slug=? AND s.seq=?",
        (slug, seq)).fetchone()
    if not s:
        raise HTTPException(404)
    block_ids = [r["id"] for r in con.execute(
        "SELECT id FROM block WHERE section_id=? ORDER BY seq", (s["id"],))]
    blocks = [block_payload(con, i) for i in block_ids]
    neighbours = {
        "prev": con.execute("SELECT seq, number_label, title FROM section "
                            "WHERE opera_id=? AND seq<? ORDER BY seq DESC "
                            "LIMIT 1", (s["opera_id"], seq)).fetchone(),
        "next": con.execute("SELECT seq, number_label, title FROM section "
                            "WHERE opera_id=? AND seq>? ORDER BY seq LIMIT 1",
                            (s["opera_id"], seq)).fetchone(),
    }
    return render("section.html", s=s, blocks=blocks, neighbours=neighbours)


# ------------------------------------------------------------------ API --

@app.get("/api/operas")
def api_operas():
    con = db()
    return [dict(r) for r in get_operas(con)]


@app.get("/api/search")
def api_search(q: str, opera: str | None = None, character: str | None = None,
               limit: int = 40):
    con = db()
    return {"query": q, "results": search_blocks(con, q, opera, character,
                                                 min(limit, 200))}


@app.get("/api/opera/{slug}/numbers")
def api_numbers(slug: str):
    con = db()
    o = con.execute("SELECT id FROM opera WHERE slug=?", (slug,)).fetchone()
    if not o:
        raise HTTPException(404)
    return [dict(r) for r in con.execute(
        "SELECT seq, act, kind, number_label, title, source_url FROM section "
        "WHERE opera_id=? ORDER BY seq", (o["id"],))]


@app.get("/api/opera/{slug}/characters")
def api_characters(slug: str):
    con = db()
    o = con.execute("SELECT id FROM opera WHERE slug=?", (slug,)).fetchone()
    if not o:
        raise HTTPException(404)
    return [dict(r) for r in con.execute(
        "SELECT name, description, is_group FROM character WHERE opera_id=? "
        "ORDER BY is_group, name", (o["id"],))]


@app.get("/api/section/{slug}/{seq}")
def api_section(slug: str, seq: int):
    con = db()
    s = con.execute(
        "SELECT s.id FROM section s JOIN opera o ON s.opera_id=o.id "
        "WHERE o.slug=? AND s.seq=?", (slug, seq)).fetchone()
    if not s:
        raise HTTPException(404)
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM block WHERE section_id=? ORDER BY seq", (s["id"],))]
    return {"blocks": [block_payload(con, i) for i in ids]}


@app.get("/api/export/{slug}/{seq}")
def api_export(slug: str, seq: int, format: str = "txt",
               character: str | None = None):
    from gsdb.export import section_cues, to_srt, to_text
    con = db()
    try:
        cues = section_cues(con, slug, seq, character)
    except KeyError:
        raise HTTPException(404)
    if format == "srt":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(to_srt(cues), media_type="text/plain")
    if format == "json":
        return {"cues": cues}
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(to_text(cues))


@app.get("/api/validation")
def api_validation():
    con = db()
    return [dict(r) for r in con.execute(
        "SELECT o.slug, p.run_at, p.page, p.level, p.message "
        "FROM parse_report p JOIN opera o ON p.opera_id=o.id "
        "WHERE p.level != 'info' ORDER BY o.slug, p.page")]
