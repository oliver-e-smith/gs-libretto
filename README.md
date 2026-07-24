# G&S Libretto Database

A structured, searchable database of the fourteen Gilbert & Sullivan opera libretti,
built from a polite snapshot of the [Gilbert and Sullivan Archive](https://gsarchive.net)
(gsarchive.net), with a web UI + JSON API deployable on Vercel.

Built for programme production and subtitling work for the International
Gilbert & Sullivan Festival.

## Credit and licence

All libretto content originates from the **Gilbert and Sullivan Archive**
(https://gsarchive.net), a volunteer-run project. The Archive publishes its
content under the [Creative Commons Attribution-ShareAlike 4.0 International
licence](https://creativecommons.org/licenses/by-sa/4.0/). This project
gratefully credits the Archive and its contributors on every page, links every
search result back to the original Archive page, and re-shares under the same
licence. The sung texts themselves (W. S. Gilbert, d. 1911) are in the public
domain.

The crawler is deliberately polite: single-threaded, throttled (default 4 s +
jitter between requests), resumable with conditional GETs so re-runs only
transfer changed pages, and it honours `robots.txt`.

## Layout

```
crawler/        polite, resumable crawler (snapshot gsarchive.net sections)
gsdb/           SQLite schema + parsers + validation report
webapp/         FastAPI app: search UI + JSON API (stage 3)
data/snapshot/  crawled HTML, kept byte-for-byte intact (canonical source layer)
data/gs.sqlite  build artifact — parsed database with FTS5 search
```

## Data model (the important bit)

Libretto text is modelled as **sequential blocks**; each block contains **one or
more parallel voices** (simultaneous singing, as marked in the Archive's HTML by
side-by-side table columns). Parallel voices are *never* merged into one stream.
Each voice carries a character attribution (or Chorus/ensemble label) and its
lines in order; stage directions are kept but flagged as directions, not sung
text. Every block stores a reference to the source page + anchor and the raw
HTML fragment, so results can be displayed with the Archive's own formatting.

## Stages

1. Crawler + Pirates of Penzance snapshot ← *current*
2. Pirates parsed: parallel-voice model + validation, searchable
3. Web UI + JSON API on Vercel
4. All fourteen operas + validation report page
5. Subtitle export (.txt / .srt with placeholder timings)
6. Later: private file library (programmes, scores, MIDI)

## Usage

```
pip install -r requirements.txt

# snapshot one opera section (polite; resumable — re-run any time)
python -m crawler.crawl --opera pirates

# build the database from the snapshot
python -m gsdb.build --opera pirates

# validation report
python -m gsdb.validate --opera pirates
```
