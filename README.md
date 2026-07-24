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

1. Crawler + Pirates of Penzance snapshot — code done; snapshot runs in CI
2. Pirates parsed: parallel-voice model + validation — done (fixture-tested;
   first CI run validates against the real pages)
3. Web UI + JSON API, Vercel-ready — done
4. All fourteen operas + validation report — next (verify section paths in
   `crawler/operas.py`, add per-opera expectations in `gsdb/validate.py`)
5. Subtitle export (.txt / .srt with placeholder timings) — done
6. Later: private file library (programmes, scores, MIDI)

## Deploying

1. Create an empty GitHub repo and push this directory to it.
2. In GitHub → Settings → Actions → General → Workflow permissions, enable
   **Read and write permissions** (the workflow commits the snapshot + DB).
3. Run the **Snapshot, build & validate** workflow (Actions tab) with opera
   `pirates`. It crawls politely (~4 s/page), builds `data/gs.sqlite`, runs
   the test suite and validation, and commits the results.
4. In Vercel: **Add New → Project → Import** the repo. No special settings —
   `vercel.json` does the rest. Every CI commit redeploys the site.
5. Check `/api/validation` and the validation step's output; parser fixes for
   real-page quirks belong in `gsdb/parse_webop.py` (see its docstring).

## JSON API (for humans' scripts and for Claude)

- `/api/search?q=take+any+heart&opera=pirates&character=Mabel`
- `/api/operas` · `/api/opera/pirates/numbers` · `/api/opera/pirates/characters`
- `/api/section/pirates/12` — full block/voice detail for one section
- `/api/export/pirates/12?format=srt&character=Mabel` — subtitle line list
  (txt / srt / json); simultaneous blocks are grouped so you know when two
  texts must share the screen
- `/api/validation` — parse warnings/errors
- Interactive docs at `/api/docs`

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
