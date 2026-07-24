"""Validation report for a parsed opera.

Checks the parse against expectations (number counts, parallel blocks,
character coverage) and prints a human-readable report. Exit code 1 if any
check fails, so CI can gate on it.

Usage:
    python -m gsdb.validate --opera pirates
"""

import argparse
import sys

from . import db

# Per-opera expectations. Filled in as each opera is brought online.
EXPECTATIONS = {
    "pirates": {
        "min_sections": 25,        # 27 number pages + dialogue pages on operhome
        "acts": {1, 2},
        "min_parallel_blocks": 3,  # Pirates famously has double choruses
        "must_have_characters": [
            "Mabel", "Frederic", "Ruth", "Pirate King", "Major-General",
        ],
        # spot checks: (fts query, expected attribution substring)
        "spot_checks": [
            ("take any heart but ours", "Mabel"),
            ("cat-like tread", None),
            ("very model of a modern Major-General", None),
        ],
    },
}


def validate(slug: str) -> bool:
    exp = EXPECTATIONS.get(slug)
    con = db.connect()
    row = con.execute("SELECT id, title FROM opera WHERE slug=?", (slug,)).fetchone()
    if not row:
        print(f"FAIL: opera {slug!r} not in database")
        return False
    oid = row["id"]
    ok = True

    def check(cond, label):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    print(f"Validation report — {row['title']}")

    n_sections = con.execute(
        "SELECT COUNT(*) c FROM section WHERE opera_id=?", (oid,)).fetchone()["c"]
    n_numbers = con.execute(
        "SELECT COUNT(*) c FROM section WHERE opera_id=? AND kind='number'",
        (oid,)).fetchone()["c"]
    n_blocks = con.execute(
        "SELECT COUNT(*) c FROM block b JOIN section s ON b.section_id=s.id "
        "WHERE s.opera_id=?", (oid,)).fetchone()["c"]
    n_parallel = con.execute(
        "SELECT COUNT(*) c FROM (SELECT b.id FROM block b "
        "JOIN section s ON b.section_id=s.id JOIN voice v ON v.block_id=b.id "
        "WHERE s.opera_id=? GROUP BY b.id HAVING COUNT(v.id) >= 2)",
        (oid,)).fetchone()["c"]
    n_chars = con.execute(
        "SELECT COUNT(*) c FROM character WHERE opera_id=? AND is_group=0",
        (oid,)).fetchone()["c"]
    n_dirs = con.execute(
        "SELECT COUNT(*) c FROM block b JOIN section s ON b.section_id=s.id "
        "WHERE s.opera_id=? AND b.kind='direction'", (oid,)).fetchone()["c"]

    print(f"  sections: {n_sections} ({n_numbers} numbers), blocks: {n_blocks} "
          f"({n_parallel} parallel), characters: {n_chars}, "
          f"direction blocks: {n_dirs}")

    warns = con.execute(
        "SELECT level, COUNT(*) c FROM parse_report WHERE opera_id=? "
        "AND level != 'info' GROUP BY level", (oid,)).fetchall()
    for w in warns:
        print(f"  parse {w['level']}s: {w['c']}")

    if not exp:
        print("  (no expectations registered yet — counts only)")
        return ok

    check(n_sections >= exp["min_sections"],
          f"sections >= {exp['min_sections']}")
    acts = {r["act"] for r in con.execute(
        "SELECT DISTINCT act FROM section WHERE opera_id=? AND act IS NOT NULL",
        (oid,))}
    check(exp["acts"] <= acts, f"acts {sorted(exp['acts'])} present")
    check(n_parallel >= exp["min_parallel_blocks"],
          f"parallel blocks >= {exp['min_parallel_blocks']}")
    for name in exp["must_have_characters"]:
        found = con.execute(
            "SELECT 1 FROM character WHERE opera_id=? AND name LIKE ?",
            (oid, f"%{name}%")).fetchone()
        check(found is not None, f"character present: {name}")
    for query, want_attr in exp["spot_checks"]:
        rows = con.execute(
            "SELECT v.attribution FROM voice_fts f "
            "JOIN voice v ON v.id = f.rowid "
            "JOIN block b ON v.block_id = b.id "
            "JOIN section s ON b.section_id = s.id "
            "WHERE s.opera_id=? AND voice_fts MATCH ?",
            (oid, f'"{query}"')).fetchall()
        hit = bool(rows) and (want_attr is None or any(
            want_attr.lower() in (r["attribution"] or "").lower() for r in rows))
        check(hit, f"search finds: “{query}”" +
              (f" (sung by {want_attr})" if want_attr else ""))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--opera", required=True)
    args = ap.parse_args()
    sys.exit(0 if validate(args.opera) else 1)


if __name__ == "__main__":
    main()
