"""Subtitle export: clean line lists from any section.

- plain text: one sung line per row; simultaneous blocks are grouped and
  marked so you know when two texts must share the screen.
- srt: placeholder timings (3.5 s per cue) for retiming in a subtitle editor.
- optional character filter (matches attribution or linked character).

Stage directions are excluded (they are flagged in the DB, not sung text).
"""

import json
import sqlite3


def section_cues(con: sqlite3.Connection, slug: str, seq: int,
                 character: str | None = None) -> list[dict]:
    """Return cues in order. A cue is {'voices': [{'attribution', 'line'}]}
    — more than one voice in a cue means simultaneous text."""
    s = con.execute(
        "SELECT s.id FROM section s JOIN opera o ON s.opera_id=o.id "
        "WHERE o.slug=? AND s.seq=?", (slug, seq)).fetchone()
    if not s:
        raise KeyError(f"{slug}/{seq}")
    cues = []
    for b in con.execute(
            "SELECT id, kind FROM block WHERE section_id=? ORDER BY seq",
            (s["id"] if isinstance(s, sqlite3.Row) else s[0],)):
        if b["kind"] == "direction":
            continue
        voices = con.execute(
            "SELECT attribution, lines_json FROM voice WHERE block_id=? "
            "ORDER BY position", (b["id"],)).fetchall()
        if character:
            cl = character.lower()
            voices = [v for v in voices
                      if cl in (v["attribution"] or "").lower()]
        parsed = []
        for v in voices:
            lines = [l["t"] for l in json.loads(v["lines_json"]) if not l["d"]]
            if lines:
                parsed.append({"attribution": v["attribution"], "lines": lines})
        if not parsed:
            continue
        if len(parsed) == 1:
            for line in parsed[0]["lines"]:
                cues.append({"voices": [{"attribution": parsed[0]["attribution"],
                                         "line": line}]})
        else:
            # simultaneous: pair lines row-by-row so each cue carries every
            # concurrent text; shorter voices simply end earlier
            depth = max(len(p["lines"]) for p in parsed)
            for i in range(depth):
                cue = [{"attribution": p["attribution"], "line": p["lines"][i]}
                       for p in parsed if i < len(p["lines"])]
                cues.append({"voices": cue, "simultaneous": True})
    return cues


def to_text(cues: list[dict]) -> str:
    out = []
    for c in cues:
        if len(c["voices"]) == 1:
            v = c["voices"][0]
            out.append(f"{(v['attribution'] or '').upper():>14}  {v['line']}"
                       if v["attribution"] else f"{'':>14}  {v['line']}")
        else:
            parts = " || ".join(
                f"[{v['attribution'] or '?'}] {v['line']}" for v in c["voices"])
            out.append(f"{'* TOGETHER':>14}  {parts}")
    return "\n".join(out) + "\n"


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def to_srt(cues: list[dict], cue_seconds: float = 3.5) -> str:
    out = []
    t = 0.0
    for i, c in enumerate(cues, 1):
        start, end = t, t + cue_seconds
        t = end
        if len(c["voices"]) == 1:
            text = c["voices"][0]["line"]
        else:
            text = "\n".join(f"[{v['attribution'] or '?'}] {v['line']}"
                             for v in c["voices"])
        out.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    return "\n".join(out)
