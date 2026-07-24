"""Build the SQLite database from the snapshot for one opera.

The web-opera home page (e.g. pirates/web_op/operhome.html) provides the
authoritative *ordered* list of number/dialogue pages, their number labels
and titles, and the act boundaries; each linked page is then parsed with
gsdb.parse_webop into blocks/voices.

Usage:
    python -m gsdb.build --opera pirates
"""

import argparse
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler import operas
from . import db
from .parse_webop import OperaConfig, parse_page, squash

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot"
BASE = "https://gsarchive.net/"


def report(con, oid, page, level, message):
    con.execute(
        "INSERT INTO parse_report(opera_id, run_at, page, level, message) "
        "VALUES(?,?,?,?,?)",
        (oid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         page, level, message))
    if level != "info":
        print(f"  [{level}] {page}: {message}")


def opera_home_pages(op: operas.Opera):
    """Yield (act, number_label, title, page_path) in performance order from
    the web-opera home page."""
    web_op = SNAP / op.site_path / "web_op"
    home = next((p for p in (web_op / "operhome.html",).__iter__() if p.exists()),
                None)
    if home is None:
        candidates = sorted(web_op.glob("*.htm*")) if web_op.exists() else []
        raise SystemExit(
            f"No operhome.html in snapshot for {op.slug}; "
            f"found {len(candidates)} pages — run the crawler first.")
    soup = BeautifulSoup(home.read_bytes(), "lxml")
    act = None
    seen = set()
    for el in soup.find_all(["a", "h1", "h2", "h3", "h4", "b", "strong"]):
        text = squash(el.get_text())
        m = re.search(r"\bAct\s+(I{1,3}|[123])\b", text, re.I)
        if m and el.name != "a":
            act = {"i": 1, "1": 1, "ii": 2, "2": 2, "iii": 3}[m.group(1).lower()]
            continue
        if el.name != "a" or not el.get("href"):
            continue
        href = el["href"].split("#")[0]
        if not re.search(r"\.htm[l]?$", href):
            continue
        page = (web_op / href).resolve()
        if not str(page).startswith(str(web_op.resolve())) or page in seen:
            continue
        if page.name in ("operhome.html", "index.html"):
            continue
        seen.add(page)
        # number label + title live in the surrounding row text
        row = el.find_parent("tr")
        context = squash(row.get_text()) if row else text
        mlabel = re.search(r"\b(No\.?s?\.?\s*[\d ,&]+|Overture|Entr'acte)", context, re.I)
        label = squash(mlabel.group(1)) if mlabel else None
        yield act, label, text, page


def build_opera(slug: str):
    op = operas.get(slug)
    con = db.connect()
    db.reset_opera(con, slug)
    con.execute(
        "INSERT OR IGNORE INTO opera(slug, title, premiere_year, site_path) "
        "VALUES(?,?,?,?)", (op.slug, op.title, op.year, op.site_path))
    oid = con.execute("SELECT id FROM opera WHERE slug=?", (slug,)).fetchone()["id"]
    cfg = OperaConfig(slug=slug)

    seq = 0
    for act, label, title, page in opera_home_pages(op):
        seq += 1
        rel = page.relative_to(SNAP)
        source_url = urljoin(BASE, str(rel).replace("\\", "/"))
        try:
            parsed = parse_page(page.read_bytes(), page.name, cfg)
        except Exception as e:
            report(con, oid, str(rel), "error", f"parse failed: {e!r}")
            continue
        kind = parsed.kind
        cur = con.execute(
            "INSERT INTO section(opera_id, act, seq, kind, number_label, title,"
            " source_page, source_url) VALUES(?,?,?,?,?,?,?,?)",
            (oid, act or parsed.act, seq, kind,
             label or parsed.number_label, title or parsed.title,
             str(rel), source_url))
        sid = cur.lastrowid
        for bseq, block in enumerate(parsed.blocks, 1):
            cur = con.execute(
                "INSERT INTO block(section_id, seq, kind, source_anchor,"
                " source_html) VALUES(?,?,?,?,?)",
                (sid, bseq, block.kind, block.source_anchor, block.source_html))
            bid = cur.lastrowid
            for pos, voice in enumerate(block.voices, 1):
                cur = con.execute(
                    "INSERT INTO voice(block_id, position, attribution, text,"
                    " lines_json) VALUES(?,?,?,?,?)",
                    (bid, pos, voice.attribution, voice.sung_text,
                     voice.lines_json()))
                if voice.attribution:
                    link_characters(con, oid, cur.lastrowid,
                                    voice.attribution, cfg)
        for w in parsed.warnings:
            report(con, oid, str(rel), "warn", w)
        nvoices = sum(len(b.voices) for b in parsed.blocks)
        report(con, oid, str(rel), "info",
               f"{len(parsed.blocks)} blocks, {nvoices} voices")
    con.commit()
    print(f"Built {slug}: {seq} sections.")


def link_characters(con, oid, voice_id, attribution, cfg: OperaConfig):
    """Resolve an attribution like 'Mabel', 'Ruth and Frederic', 'Chorus of
    Girls' to character rows (creating group entries on demand)."""
    parts = re.split(r"\s*(?:,|&| and )\s*", attribution)
    for part in (squash(p) for p in parts if squash(p)):
        is_group = cfg.is_group(part)
        row = con.execute(
            "SELECT id FROM character WHERE opera_id=? AND name=? COLLATE NOCASE",
            (oid, part)).fetchone()
        if row is None:
            cur = con.execute(
                "INSERT INTO character(opera_id, name, is_group) VALUES(?,?,?)",
                (oid, part.title() if part.isupper() else part, int(is_group)))
            cid = cur.lastrowid
        else:
            cid = row["id"]
        con.execute(
            "INSERT OR IGNORE INTO voice_character(voice_id, character_id) "
            "VALUES(?,?)", (voice_id, cid))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--opera", required=True)
    args = ap.parse_args()
    build_opera(args.opera)


if __name__ == "__main__":
    main()
