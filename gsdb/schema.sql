-- G&S Libretto Database schema (SQLite + FTS5)
--
-- Core idea: libretto text is a sequence of BLOCKS. A block holds one or
-- more parallel VOICES (simultaneous singing, from the Archive's
-- side-by-side table columns). Parallel voices are never merged.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS opera (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,     -- 'pirates'
    title         TEXT NOT NULL,            -- 'The Pirates of Penzance'
    premiere_year INTEGER,
    site_path     TEXT NOT NULL             -- section path on gsarchive.net
);

-- Dramatis personae, with the Archive's descriptive subtitles.
CREATE TABLE IF NOT EXISTS character (
    id          INTEGER PRIMARY KEY,
    opera_id    INTEGER NOT NULL REFERENCES opera(id),
    name        TEXT NOT NULL,              -- 'Mabel'
    description TEXT,                       -- '(General Stanley's Daughter)'
    is_group    INTEGER NOT NULL DEFAULT 0, -- chorus / ensemble labels
    sort_order  INTEGER,
    UNIQUE (opera_id, name)
);

-- A section of the opera in performance order: a musical number OR a spoken
-- dialogue scene between numbers.
CREATE TABLE IF NOT EXISTS section (
    id          INTEGER PRIMARY KEY,
    opera_id    INTEGER NOT NULL REFERENCES opera(id),
    act         INTEGER,                    -- 1, 2 (0 = front matter)
    seq         INTEGER NOT NULL,           -- performance order within opera
    kind        TEXT NOT NULL CHECK (kind IN ('number', 'dialogue')),
    number_label TEXT,                      -- 'No. 9 & 10', 'Overture'; NULL for dialogue
    title       TEXT,                       -- first-line title, e.g.
                                            -- 'Poor wandering one!'
    source_page TEXT NOT NULL,              -- snapshot-relative path of source HTML
    source_url  TEXT NOT NULL,              -- live gsarchive.net URL
    UNIQUE (opera_id, seq)
);

-- Sequential blocks within a section.
CREATE TABLE IF NOT EXISTS block (
    id            INTEGER PRIMARY KEY,
    section_id    INTEGER NOT NULL REFERENCES section(id),
    seq           INTEGER NOT NULL,         -- order within section
    kind          TEXT NOT NULL CHECK (kind IN ('sung', 'spoken', 'direction')),
    source_anchor TEXT,                     -- nearest id/name anchor on the page
    source_html   TEXT,                     -- raw HTML fragment (canonical display)
    UNIQUE (section_id, seq)
);

-- Voices within a block. position > 1 means simultaneous with position 1.
CREATE TABLE IF NOT EXISTS voice (
    id          INTEGER PRIMARY KEY,
    block_id    INTEGER NOT NULL REFERENCES block(id),
    position    INTEGER NOT NULL,           -- column order, 1-based
    attribution TEXT,                       -- display label: 'Mabel', 'Girls',
                                            -- 'Edith and Kate'; NULL for a
                                            -- direction-only block
    text        TEXT NOT NULL,              -- normalised searchable text,
                                            -- lines joined with '\n'
                                            -- (directions excluded)
    lines_json  TEXT NOT NULL,              -- [{"t": "...", "d": 0|1}, ...]
                                            -- d=1 flags an inline stage direction
    UNIQUE (block_id, position)
);

-- Resolved link(s) from a voice's attribution to dramatis personae entries.
CREATE TABLE IF NOT EXISTS voice_character (
    voice_id     INTEGER NOT NULL REFERENCES voice(id),
    character_id INTEGER NOT NULL REFERENCES character(id),
    PRIMARY KEY (voice_id, character_id)
);

-- Full-text search over voice text (external-content FTS5).
CREATE VIRTUAL TABLE IF NOT EXISTS voice_fts USING fts5(
    text,
    content='voice',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS voice_ai AFTER INSERT ON voice BEGIN
    INSERT INTO voice_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS voice_ad AFTER DELETE ON voice BEGIN
    INSERT INTO voice_fts(voice_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS voice_au AFTER UPDATE ON voice BEGIN
    INSERT INTO voice_fts(voice_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO voice_fts(rowid, text) VALUES (new.id, new.text);
END;

-- Parse-run bookkeeping for the validation report.
CREATE TABLE IF NOT EXISTS parse_report (
    id         INTEGER PRIMARY KEY,
    opera_id   INTEGER NOT NULL REFERENCES opera(id),
    run_at     TEXT NOT NULL,
    page       TEXT NOT NULL,
    level      TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
    message    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_section_opera ON section(opera_id, seq);
CREATE INDEX IF NOT EXISTS idx_block_section ON block(section_id, seq);
CREATE INDEX IF NOT EXISTS idx_voice_block   ON voice(block_id, position);
