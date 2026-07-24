"""Parse G&S Archive "web opera" pages into the block/voice model.

The Archive's HTML is hand-authored over ~25 years and inconsistent between
operas, so this parser is deliberately tolerant and hook-based: OperaConfig
lets each opera override quirk handling, and anything suspicious is logged
rather than crashing the build.

Markup conventions handled (observed on the Pirates of Penzance pages):

- Number pages: heading like "No. 1: OPENING CHORUS & SOLO (Samuel)", then
  the song text laid out in tables where the LEFT column holds the character
  label ("Samuel.") and the RIGHT column the lines. Rows with an empty label
  cell continue the current voice.
- Simultaneous singing: a table row with >= 2 substantive text columns; each
  column is a separate voice (its own attribution heading its column).
  Columns are never merged.
- Dialogue pages (…d.html): "Dialogue following No. 5" heading; speeches are
  paragraphs opening with a bold speaker name: <b>Kate.</b> spoken text…
- Stage/scene directions: italicised runs and/or [bracketed]/(parenthesised)
  text; kept, flagged is_direction.
- Page furniture (breadcrumbs, MIDI/karaoke links, credits) is dropped:
  chunks that are mostly link text.
"""

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

DIRECTION_RE = re.compile(r"^\s*[\[(].*[\])]\s*[.]?\s*$", re.S)
ATTRIB_RE = re.compile(r"^\s*([A-Z][A-Za-z .,'&’\-]{0,60}?)\s*[.:]?\s*$")
WS_RE = re.compile(r"\s+")
NOISE_RE = re.compile(
    r"^(archive home|web opera|act [iv12]+|top of (the )?page|midi|karaoke|"
    r"previous|next|contents|home|the pirates|feedback)\b", re.I)


def squash(s: str) -> str:
    return WS_RE.sub(" ", s).strip()


# ----------------------------------------------------------------- model --

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
    group_words: tuple = ("chorus", "girls", "pirates", "police", "ensemble",
                          "all", "men", "women", "ladies", "peers", "fairies",
                          "guards", "citizens", "bridesmaids", "daughters")
    # max words for a table cell to count as a label column
    label_max_words: int = 4

    def is_group(self, attribution: str) -> bool:
        a = attribution.lower()
        return any(re.search(rf"\b{w}\b", a) for w in self.group_words)


# ---------------------------------------------------------------- chunks --

@dataclass
class Chunk:
    text: str
    is_direction: bool
    attr: str | None       # leading bold speaker name, if any
    link_ratio: float      # fraction of characters inside <a>


def _iter_chunks(container: Tag):
    """Yield Chunks from a content container, splitting on <br> and block
    elements. Tracks italic/link character counts and a leading bold run."""
    out: list[Chunk] = []
    state = {"parts": [], "ital": 0, "link": 0, "total": 0, "lead_bold": None,
             "seen_plain": False}

    def flush():
        text = squash("".join(state["parts"]))
        if text:
            total = max(state["total"], 1)
            is_dir = state["ital"] / total >= 0.8 or bool(DIRECTION_RE.match(text))
            attr = None
            lb = state["lead_bold"]
            if lb:
                m = ATTRIB_RE.match(lb)
                if m and squash(m.group(1)):
                    attr = squash(m.group(1))
                    lead = squash(lb)
                    if text.startswith(lead):
                        text = squash(text[len(lead):])
                    is_dir = False if text else is_dir
            out.append(Chunk(text, is_dir, attr, state["link"] / total))
        state.update(parts=[], ital=0, link=0, total=0, lead_bold=None,
                     seen_plain=False)

    def add(text: str, *, ital=False, link=False, bold=False):
        if not text.strip() and not state["parts"]:
            return
        state["parts"].append(text)
        n = len(text)
        state["total"] += n
        if ital:
            state["ital"] += n
        if link:
            state["link"] += n
        if bold and not state["seen_plain"] and state["lead_bold"] is None:
            state["lead_bold"] = text
        elif text.strip() and not bold:
            state["seen_plain"] = True

    def walk(node, ital=False, link=False, bold=False):
        for child in node.children:
            if isinstance(child, NavigableString):
                add(str(child), ital=ital, link=link, bold=bold)
            elif isinstance(child, Tag):
                name = child.name
                if name == "br":
                    flush()
                elif name in ("p", "div", "tr", "li", "blockquote", "h1", "h2",
                              "h3", "h4", "h5", "center"):
                    flush()
                    walk(child, ital, link, bold)
                    flush()
                elif name in ("i", "em"):
                    walk(child, True, link, bold)
                elif name in ("b", "strong"):
                    walk(child, ital, link, True)
                elif name == "a":
                    walk(child, ital, True, bold)
                elif name in ("script", "style", "img", "map"):
                    continue
                else:
                    walk(child, ital, link, bold)

    walk(container)
    flush()
    return out


def is_noise(chunk: Chunk) -> bool:
    return chunk.link_ratio > 0.8 or bool(NOISE_RE.match(chunk.text)) \
        or chunk.text in {"|", "-", "·"}


# ---------------------------------------------------------------- tables --

def classify_table(table: Tag, cfg: OperaConfig) -> str:
    """'parallel' | 'labelled' | 'layout'"""
    parallel_rows = labelled_rows = 0
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        texty = [c for c in cells if len(squash(c.get_text()).split()) >
                 cfg.label_max_words]
        if len(texty) >= 2:
            parallel_rows += 1
        elif len(cells) >= 2 and len(texty) <= 1:
            first = squash(cells[0].get_text())
            if (not first or ATTRIB_RE.match(first)) and any(
                    squash(c.get_text()) for c in cells[1:]):
                labelled_rows += 1
    if parallel_rows:
        return "parallel"
    if labelled_rows:
        return "labelled"
    return "layout"


def _parse_parallel_cell(cell: Tag) -> tuple[str | None, list[Line]]:
    """One table cell of a parallel row -> (explicit attribution, lines)."""
    attr: str | None = None
    lines: list[Line] = []
    for ch in _iter_chunks(cell):
        if is_noise(ch):
            continue
        if ch.attr and attr is None and not lines:
            attr = ch.attr
            if ch.text:
                lines.append(Line(ch.text, ch.is_direction))
            continue
        if attr is None and not lines and not ch.is_direction:
            m = ATTRIB_RE.match(ch.text)
            if m and len(ch.text.split()) <= 4:
                attr = squash(m.group(1))
                continue
        if ch.text:
            prefix = f"{ch.attr}. " if ch.attr else ""
            lines.append(Line(prefix + ch.text, ch.is_direction))
    return attr, lines


def parse_parallel_table(table: Tag, warnings: list[str]) -> list[list[Voice]]:
    """A parallel table is a sequence of simultaneous moments.

    Each row with >= 2 substantive columns yields one group of parallel
    voices (a block). Singers may change from row to row within a column;
    when a row's column has no explicit attribution it inherits the
    attribution from the same column of the previous row (a continuation).
    Consecutive continuation rows are merged into the previous block so a
    stanza split across rows stays one block.
    """
    groups: list[list[Voice]] = []
    prev_attrs: dict[int, str | None] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        parsed = [(_i, *_parse_parallel_cell(c)) for _i, c in enumerate(cells)]
        parsed = [(i, a, ls) for i, a, ls in parsed if a or ls]
        if not parsed:
            continue
        any_new_attr = any(a for _, a, _ in parsed)
        if groups and not any_new_attr:
            # continuation row: append lines down the matching columns
            current = groups[-1]
            for (i, _, ls) in parsed:
                target = current[min(i, len(current) - 1)]
                target.lines.extend(ls)
            continue
        voices = []
        for (i, a, ls) in parsed:
            attribution = a or prev_attrs.get(i)
            voices.append(Voice(attribution=attribution, lines=ls))
            prev_attrs[i] = attribution
        groups.append(voices)
    if not groups or all(len(g) < 2 for g in groups):
        warnings.append("parallel table produced no multi-voice row")
    return groups


# ----------------------------------------------------------------- pages --

HEAD_NO_RE = re.compile(
    r"\b(No\.?s?\.?\s*\d+[\d ,&and]*|Overture|Entr'acte)\b", re.I)
DIALOGUE_HEAD_RE = re.compile(r"Dialogue\s+(following|after|before)\s+(No\.?\s*\d+)",
                              re.I)
ACT_RE = re.compile(r"\bAct\s+(I{1,3}\b|[123]\b)", re.I)
ROMAN = {"i": 1, "1": 1, "ii": 2, "2": 2, "iii": 3, "3": 3}


class _PageParser:
    def __init__(self, filename: str, cfg: OperaConfig):
        self.cfg = cfg
        self.is_dialogue = bool(re.search(r"\dd\.html?$", filename))
        self.default_kind = "spoken" if self.is_dialogue else "sung"
        self.blocks: list[Block] = []
        self.voice: Voice | None = None
        self.anchor: str | None = None
        self.anchor_el: Tag | None = None
        self.warnings: list[str] = []
        self.number_label: str | None = None
        self.title: str | None = None
        self.title_is_first_line = False
        self.act: int | None = None

    def close_voice(self):
        v, self.voice = self.voice, None
        if v and (v.lines or v.attribution):
            if not v.lines:
                return
            kind = ("direction" if all(l.is_direction for l in v.lines)
                    else self.default_kind)
            self.blocks.append(Block(kind, [v], self.anchor,
                                     str(self.anchor_el) if self.anchor_el else ""))

    def feed_headings_only(self, el: Tag):
        for ch in _iter_chunks(el):
            if not is_noise(ch):
                self.scan_heading(ch.text)

    def feed_flow(self, el: Tag):
        chunks = [ch for ch in _iter_chunks(el) if not is_noise(ch)]
        # A whole-paragraph stage direction stands alone (it should not be
        # swallowed into the previous speaker's voice).
        if chunks and all(c.is_direction and not c.attr for c in chunks):
            self.close_voice()
            self.blocks.append(Block(
                "direction",
                [Voice(None, [Line(c.text, True) for c in chunks if c.text])],
                self.anchor, str(el)))
            return
        for ch in chunks:
            self.scan_heading(ch.text)
            if ch.attr:
                self.close_voice()
                self.voice = Voice(attribution=ch.attr)
                self.anchor_el = el
                if ch.text:
                    self.voice.lines.append(Line(ch.text, ch.is_direction))
                continue
            if not ch.text:
                continue
            if self.voice is None:
                if ch.is_direction:
                    self.blocks.append(Block(
                        "direction", [Voice(None, [Line(ch.text, True)])],
                        self.anchor, str(el)))
                    continue
                # unattributed sung/spoken text (e.g. continuation)
                self.voice = Voice(attribution=None)
                self.anchor_el = el
            self.voice.lines.append(Line(ch.text, ch.is_direction))

    def feed_labelled_table(self, table: Tag):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if not cells:
                continue
            label = squash(cells[0].get_text()) if len(cells) >= 2 else ""
            content_cells = cells[1:] if len(cells) >= 2 else cells
            if label and ATTRIB_RE.match(label) and \
                    len(label.split()) <= self.cfg.label_max_words:
                self.close_voice()
                self.voice = Voice(attribution=squash(
                    ATTRIB_RE.match(label).group(1)))
                self.anchor_el = row
            for cell in content_cells:
                for ch in _iter_chunks(cell):
                    if is_noise(ch):
                        continue
                    if ch.attr:
                        self.close_voice()
                        self.voice = Voice(attribution=ch.attr)
                        self.anchor_el = row
                        if ch.text:
                            self.voice.lines.append(Line(ch.text, ch.is_direction))
                        continue
                    if not ch.text:
                        continue
                    if self.voice is None:
                        self.voice = Voice(attribution=None)
                        self.anchor_el = row
                    self.voice.lines.append(Line(ch.text, ch.is_direction))

    def scan_heading(self, text: str):
        if self.is_dialogue:
            m = DIALOGUE_HEAD_RE.search(text)
            if m and self.number_label is None:
                self.number_label = squash(m.group(0))
                return
        m = HEAD_NO_RE.search(text)
        if m and len(text) < 120:
            if self.number_label is None:
                self.number_label = squash(m.group(1))
            rest = squash(text[m.end():].lstrip(" :-–—."))
            if rest and self.title is None and \
                    squash(m.group(1)) in (self.number_label or ""):
                self.title = rest
        # a fully-quoted heading line is the first-line title and takes
        # precedence over a genre heading like "OPENING CHORUS & SOLO"
        q = re.match(r'^[“"]([^”"]+)[”"]$', squash(text))
        if q and not self.title_is_first_line:
            self.title = squash(q.group(1))
            self.title_is_first_line = True
        m = ACT_RE.search(text)
        if m and self.act is None:
            self.act = ROMAN.get(m.group(1).lower())

    def update_anchor(self, el: Tag):
        for prev in [el] + list(el.find_all_previous(True, limit=200)):
            if prev.name == "a" and prev.get("name"):
                self.anchor = prev["name"]
                return
            if prev.get("id"):
                self.anchor = prev["id"]
                return


def parse_page(html: bytes, filename: str, cfg: OperaConfig) -> ParsedSection:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    pp = _PageParser(filename, cfg)

    if soup.title:
        pp.scan_heading(squash(soup.title.get_text()))

    for el in body.find_all(["p", "table", "blockquote", "h1", "h2", "h3",
                             "h4", "h5", "center"], recursive=True):
        if el.find_parent("table") is not None:
            continue
        if el.name not in ("p", "table") and el.find(["p", "table"]) is not None:
            continue  # container that will be visited via its children
        pp.update_anchor(el)
        if el.name in ("h1", "h2", "h3", "h4", "h5"):
            pp.feed_headings_only(el)
            continue
        if el.name == "table":
            klass = classify_table(el, cfg)
            if klass == "parallel":
                pp.close_voice()
                for voices in parse_parallel_table(el, pp.warnings):
                    if voices:
                        pp.blocks.append(Block("sung", voices, pp.anchor,
                                               str(el)))
            elif klass == "labelled":
                pp.feed_labelled_table(el)
            else:
                for cell in el.find_all(["td", "th"]):
                    if cell.find("table") is None:
                        pp.feed_flow(cell)
        else:
            pp.feed_flow(el)
    pp.close_voice()

    return ParsedSection("dialogue" if pp.is_dialogue else "number",
                         pp.number_label, pp.title, pp.act, pp.blocks,
                         pp.warnings)
