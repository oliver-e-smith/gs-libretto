"""Parse G&S Archive "web opera" pages into the block/voice model.

The Archive's HTML is hand-authored over ~25 years and inconsistent between
operas, so this parser is deliberately tolerant and hook-based: OperaConfig
lets each opera override page discovery and quirk handling, and everything
suspicious is logged to parse_report rather than crashing the build.

General conventions observed on web-opera pages (verified against the
Pirates of Penzance snapshot; other operas may need per-opera hooks):

- A musical number page contains headings with the number label and title,
  then a run of paragraphs/tables with the sung text.
- Character attribution is a bold (or ALL-CAPS) run ending with '.' or ':'
  at the start of a passage, e.g. MABEL.
- Simultaneous singing is a <table> whose row(s) hold two or more cells of
  sung text side by side, each column usually headed by its own character
  name. Columns become parallel voices, never merged.
- Stage directions are italicised and/or parenthesised.
- Dialogue pages (…d.html) are spoken scenes between numbers.
"""

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

DIRECTION_RE = re.compile(r"^\s*[\[(].*[\])]\s*$", re.S)
ATTRIB_RE = re.compile(r"^\s*([A-Z][A-Za-z .,'&\-]+?)\s*[.:]\s*$")
WS_RE = re.compile(r"\s+")


def squash(s: str) -> str:
    return WS_RE.sub(" ", s).strip()


@dataclass
class Line:
    text: str
    is_direction: bool = False


@dataclass
class Voice:
    attribution: str | None
    lines: list[Line] = field(default_factory=list)

    @property
    def sung_text(self) -> str:
        return "\n".join(l.text for l in self.lines if not l.is_direction)

    def lines_json(self) -> str:
        return json.dumps([{"t": l.text, "d": int(l.is_direction)}
                           for l in self.lines], ensure_ascii=False)


@dataclass
class Block:
    kind: str                    # 'sung' | 'spoken' | 'direction'
    voices: list[Voice]
    source_anchor: str | None
    source_html: str


@dataclass
class ParsedSection:
    kind: str                    # 'number' | 'dialogue'
    number_label: str | None
    title: str | None
    act: int | None
    blocks: list[Block]
    warnings: list[str]


@dataclass
class OperaConfig:
    """Per-opera quirk hooks; defaults suit Pirates."""
    slug: str = ""
    # map page filename -> (act, number_label, title) overrides
    page_overrides: dict = field(default_factory=dict)
    # attributions that mean a group/chorus rather than a named character
    group_words: tuple = ("chorus", "girls", "pirates", "police", "ensemble",
                          "all", "men", "women", "ladies", "peers", "fairies",
                          "guards", "citizens", "bridesmaids")

    def is_group(self, attribution: str) -> bool:
        a = attribution.lower()
        return any(w in a for w in self.group_words)


def looks_direction(text: str, el: Tag | None = None) -> bool:
    if DIRECTION_RE.match(text):
        return True
    if el is not None and el.find(["i", "em"]) is not None:
        # fully-italic content with no plain text siblings
        plain = squash("".join(
            s for s in el.strings
            if not any(p.name in ("i", "em") for p in s.parents)))
        return plain == ""
    return False


def nearest_anchor(el: Tag) -> str | None:
    """Nearest preceding <a name=…> or element id, walking backwards."""
    for prev in el.find_all_previous(True):
        if prev.name == "a" and prev.get("name"):
            return prev["name"]
        if prev.get("id"):
            return prev["id"]
    return None


def split_attribution(text: str) -> str | None:
    m = ATTRIB_RE.match(text)
    return squash(m.group(1)) if m else None


def cell_to_voice(cell: Tag, default_attr: str | None) -> Voice:
    """Turn one table cell (a parallel column) into a Voice."""
    voice = Voice(attribution=default_attr)
    for chunk in _chunks(cell):
        text, is_dir = chunk
        if not voice.lines and not is_dir and voice.attribution is None:
            att = split_attribution(text)
            if att:
                voice.attribution = att
                continue
        voice.lines.append(Line(text, is_dir))
    return voice


def _chunks(container: Tag):
    """Yield (line_text, is_direction) from a content container, splitting on
    <br> and block elements, flagging italic/parenthesised runs."""
    buf: list[str] = []
    buf_dir: list[bool] = []

    def flush():
        text = squash("".join(buf))
        if text:
            is_dir = (all(buf_dir) and buf_dir) or bool(DIRECTION_RE.match(text))
            yielded.append((text, bool(is_dir)))
        buf.clear()
        buf_dir.clear()

    yielded: list[tuple[str, bool]] = []

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                s = str(child)
                if s.strip():
                    buf.append(s)
                    buf_dir.append(False)
            elif isinstance(child, Tag):
                if child.name == "br":
                    flush()
                elif child.name in ("i", "em"):
                    t = squash(child.get_text())
                    if t:
                        buf.append(t)
                        buf_dir.append(True)
                elif child.name in ("p", "div", "tr", "li", "blockquote"):
                    flush()
                    walk(child)
                    flush()
                else:
                    walk(child)

    walk(container)
    flush()
    return yielded


def parse_page(html: bytes, filename: str, cfg: OperaConfig) -> ParsedSection:
    """Parse one web-opera page into a ParsedSection.

    NOTE: tuned against the Pirates snapshot; run gsdb.validate after any
    change and inspect the report before trusting output.
    """
    soup = BeautifulSoup(html, "lxml")
    warnings: list[str] = []
    body = soup.body or soup
    is_dialogue = re.search(r"d\.html?$", filename) is not None

    number_label = title = None
    act = None
    # Heading extraction: archive pages usually put 'No. 12' and the title in
    # <h*> or bold-centered paragraphs near the top.
    for h in body.find_all(["h1", "h2", "h3", "h4", "center", "p"], limit=12):
        t = squash(h.get_text())
        if not t:
            continue
        m = re.search(r"\b(No\.?s?\.?\s*[\d ,&and]+|Overture|Entr'acte)\b", t, re.I)
        if m and number_label is None:
            number_label = squash(m.group(1))
        m = re.search(r"\bAct\s+(I{1,3}|[12])\b", t, re.I)
        if m and act is None:
            act = {"i": 1, "1": 1, "ii": 2, "2": 2, "iii": 3}.get(m.group(1).lower())
        if number_label and title is None and '"' in t:
            q = re.search(r'[“"]([^”"]+)[”"]', t)
            if q:
                title = squash(q.group(1))

    blocks: list[Block] = []
    current_voice: Voice | None = None
    default_kind = "spoken" if is_dialogue else "sung"

    def close_voice():
        nonlocal current_voice
        if current_voice and current_voice.lines:
            kind = default_kind
            if all(l.is_direction for l in current_voice.lines):
                kind = "direction"
            blocks.append(Block(kind, [current_voice],
                                nearest_anchor(anchor_el) if anchor_el else None,
                                str(anchor_el) if anchor_el else ""))
        current_voice = None

    anchor_el: Tag | None = None

    for el in body.find_all(["p", "table", "blockquote"], recursive=True):
        # skip nested containers handled via their parent table
        if el.find_parent("table") is not None:
            continue
        anchor_el = el
        if el.name == "table":
            rows = el.find_all("tr", recursive=False) or el.find_all("tr")
            # widest row decides voice count
            width = max((len(r.find_all(["td", "th"], recursive=False))
                         for r in rows), default=0)
            if width >= 2 and _table_is_parallel(el):
                close_voice()
                voices = _parse_parallel_table(el, cfg, warnings)
                if voices:
                    blocks.append(Block("sung", voices, nearest_anchor(el), str(el)))
                continue
            # single-column layout table: treat cells as flow content
            for cell in el.find_all(["td", "th"]):
                current_voice = _flow(cell, current_voice, blocks, default_kind, el)
            continue
        current_voice = _flow(el, current_voice, blocks, default_kind, el)
    close_voice()

    return ParsedSection("dialogue" if is_dialogue else "number",
                         number_label, title, act, blocks, warnings)


def _flow(container: Tag, current_voice: Voice | None, blocks: list[Block],
          default_kind: str, anchor_el: Tag) -> Voice | None:
    """Feed flowing (non-parallel) content into voices/blocks."""
    for text, is_dir in _chunks(container):
        att = split_attribution(text) if not is_dir else None
        if att:
            _close(current_voice, blocks, default_kind, anchor_el)
            current_voice = Voice(attribution=att)
            continue
        if is_dir and current_voice is None:
            blocks.append(Block("direction", [Voice(None, [Line(text, True)])],
                                nearest_anchor(anchor_el), str(anchor_el)))
            continue
        if current_voice is None:
            current_voice = Voice(attribution=None)
        current_voice.lines.append(Line(text, is_dir))
    return current_voice


def _close(voice: Voice | None, blocks: list[Block], default_kind: str,
           anchor_el: Tag):
    if voice and voice.lines:
        kind = default_kind
        if all(l.is_direction for l in voice.lines):
            kind = "direction"
        blocks.append(Block(kind, [voice], nearest_anchor(anchor_el),
                            str(anchor_el)))


def _table_is_parallel(table: Tag) -> bool:
    """Heuristic: a table is a simultaneous-singing layout if some row has
    >= 2 cells that each contain multi-word text."""
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        texty = [c for c in cells if len(squash(c.get_text()).split()) >= 3]
        if len(texty) >= 2:
            return True
    return False


def _parse_parallel_table(table: Tag, cfg: OperaConfig,
                          warnings: list[str]) -> list[Voice]:
    """Each column of the table becomes one Voice; rows are concatenated
    down each column so a column's lines stay in order."""
    columns: dict[int, Voice] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        for i, cell in enumerate(cells):
            v = columns.setdefault(i, Voice(attribution=None))
            for text, is_dir in _chunks(cell):
                if v.attribution is None and not v.lines and not is_dir:
                    att = split_attribution(text)
                    if att:
                        v.attribution = att
                        continue
                v.lines.append(Line(text, is_dir))
    voices = [v for _, v in sorted(columns.items()) if v.lines or v.attribution]
    if len(voices) < 2:
        warnings.append("parallel table collapsed to <2 voices")
    return voices
