"""Polite, resumable crawler for gsarchive.net opera sections.

Design goals (in order): be gentle to a volunteer-run site, be resumable,
keep pages byte-for-byte intact.

- single-threaded, throttled: default 4 s + jitter between *network* requests
- honours robots.txt (and never fetches outside gsarchive.net)
- resumable: data/snapshot/manifest.json records ETag / Last-Modified / sha256
  per URL; re-runs issue conditional GETs, and a 304 costs the site almost
  nothing. Interrupt at any time; state is flushed after every fetch.
- scope: only pages under the opera's section path, plus same-host assets
  (images/css) referenced by saved pages. Big media (zip/pdf/mid) is recorded
  in the manifest as "skipped" unless --include-media is given.

Usage:
    python -m crawler.crawl --opera pirates
    python -m crawler.crawl --opera pirates --delay 6 --max-pages 40
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from . import operas

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot"
MANIFEST = SNAP / "manifest.json"

BASE = "https://gsarchive.net/"
HOST = "gsarchive.net"
UA = ("GS-Libretto-Project/0.1 (+polite snapshot for a libretto database; "
      "contact: oliver@theatricaladventures.com)")

PAGE_EXT = {".html", ".htm", ""}
ASSET_EXT = {".gif", ".jpg", ".jpeg", ".png", ".css", ".ico", ".svg", ".webp"}
MEDIA_EXT = {".zip", ".pdf", ".mid", ".midi", ".kar", ".mp3", ".doc", ".txt"}


def norm_url(url: str) -> str:
    """Normalise: strip fragment + query, resolve, lowercase host."""
    s = urlsplit(url)
    if s.scheme not in ("http", "https", ""):
        return ""
    netloc = s.netloc.lower().removeprefix("www.")
    if netloc and netloc != HOST:
        return ""
    path = re.sub(r"/{2,}", "/", s.path) or "/"
    if path.endswith("/"):
        path += "index.html"
    return urlunsplit(("https", HOST, path, "", ""))


def local_path(url: str) -> Path:
    return SNAP / urlsplit(url).path.lstrip("/")


def ext_of(url: str) -> str:
    path = urlsplit(url).path
    dot = path.rfind(".")
    return path[dot:].lower() if dot > path.rfind("/") else ""


class Manifest:
    def __init__(self):
        self.data = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}

    def save(self):
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(self.data, indent=1, sort_keys=True))

    def entry(self, url):
        return self.data.get(url, {})

    def record(self, url, **kw):
        self.data.setdefault(url, {}).update(kw)


class Crawler:
    def __init__(self, opera: operas.Opera, delay: float, max_pages: int,
                 include_media: bool, refresh: bool):
        self.opera = opera
        self.delay = delay
        self.max_pages = max_pages
        self.include_media = include_media
        self.refresh = refresh  # re-check pages already in manifest (conditional GET)
        self.manifest = Manifest()
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=30,
                                   follow_redirects=True)
        self.robots = urllib.robotparser.RobotFileParser()
        self.fetched = 0
        self.last_request = 0.0

    # -- politeness -----------------------------------------------------
    def throttle(self):
        wait = self.delay + random.uniform(0, self.delay / 2)
        elapsed = time.monotonic() - self.last_request
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self.last_request = time.monotonic()

    def load_robots(self):
        self.throttle()
        r = self.client.get(urljoin(BASE, "/robots.txt"))
        self.robots.parse(r.text.splitlines() if r.status_code == 200 else [])

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(UA, url)

    # -- fetching -------------------------------------------------------
    def fetch(self, url: str) -> bytes | None:
        """Fetch url politely with conditional GET; save to snapshot; return
        body bytes if (re)fetched or already on disk, else None."""
        ent = self.manifest.entry(url)
        path = local_path(url)
        if ent.get("status") == "ok" and path.exists() and not self.refresh:
            return path.read_bytes()
        if not self.allowed(url):
            self.manifest.record(url, status="robots_disallowed")
            return None
        headers = {}
        if path.exists():
            if ent.get("etag"):
                headers["If-None-Match"] = ent["etag"]
            if ent.get("last_modified"):
                headers["If-Modified-Since"] = ent["last_modified"]
        self.throttle()
        try:
            r = self.client.get(url, headers=headers)
        except httpx.HTTPError as e:
            print(f"  !! {url}: {e}", file=sys.stderr)
            self.manifest.record(url, status=f"error:{type(e).__name__}")
            return None
        finally:
            self.manifest.save()
        if r.status_code == 304:
            self.manifest.record(url, status="ok", checked=now_iso())
            return path.read_bytes()
        if r.status_code == 429 or r.status_code >= 500:
            retry = int(r.headers.get("Retry-After", 60))
            print(f"  .. {r.status_code}, backing off {retry}s", file=sys.stderr)
            time.sleep(min(retry, 300))
            self.manifest.record(url, status=f"retry_later:{r.status_code}")
            return None
        if r.status_code != 200:
            self.manifest.record(url, status=f"http_{r.status_code}")
            return None
        body = r.content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.fetched += 1
        self.manifest.record(
            url, status="ok", path=str(path.relative_to(ROOT)),
            etag=r.headers.get("ETag"), last_modified=r.headers.get("Last-Modified"),
            sha256=hashlib.sha256(body).hexdigest(), fetched=now_iso(),
            bytes=len(body))
        self.manifest.save()
        print(f"  ok {url} ({len(body):,} B)")
        return body

    # -- scoping --------------------------------------------------------
    def in_scope(self, url: str) -> bool:
        return urlsplit(url).path.lstrip("/").startswith(self.opera.site_path + "/")

    def extract_links(self, base_url: str, body: bytes):
        soup = BeautifulSoup(body, "lxml")
        pages, assets = [], []
        for tag, attr in (("a", "href"), ("frame", "src"), ("iframe", "src")):
            for el in soup.find_all(tag, **{attr: True}):
                u = norm_url(urljoin(base_url, el[attr]))
                if not u:
                    continue
                e = ext_of(u)
                if e in PAGE_EXT and self.in_scope(u):
                    pages.append(u)
                elif e in MEDIA_EXT and self.in_scope(u):
                    if self.include_media:
                        assets.append(u)
                    else:
                        self.manifest.record(u, status="skipped_media")
        for el in soup.find_all(["img", "script"], src=True) + \
                  soup.find_all("link", href=True):
            u = norm_url(urljoin(base_url, el.get("src") or el.get("href") or ""))
            if u and ext_of(u) in ASSET_EXT:
                assets.append(u)
        return pages, assets

    # -- main loop ------------------------------------------------------
    def run(self):
        print(f"Snapshotting {self.opera.title} -> {SNAP}")
        self.load_robots()
        seeds = [norm_url(urljoin(BASE, s)) for s in self.opera.seeds] or \
                [norm_url(urljoin(BASE, self.opera.site_path + "/html/index.html"))]
        queue: list[str] = [s for s in seeds if s]
        seen: set[str] = set(queue)
        while queue:
            if self.max_pages and self.fetched >= self.max_pages:
                print(f"Stopping at --max-pages={self.max_pages} (resumable).")
                break
            url = queue.pop(0)
            body = self.fetch(url)
            if body is None or ext_of(url) not in PAGE_EXT:
                continue
            pages, assets = self.extract_links(url, body)
            for u in assets:
                if u not in seen:
                    seen.add(u)
                    self.fetch(u)
            for u in pages:
                if u not in seen:
                    seen.add(u)
                    queue.append(u)
        self.manifest.save()
        ok = sum(1 for e in self.manifest.data.values() if e.get("status") == "ok")
        print(f"Done. {self.fetched} fetched this run; {ok} files ok in snapshot.")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--opera", required=True, help="opera slug, e.g. pirates")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="seconds between requests (plus jitter); default 4")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="stop after N fetches this run (0 = no limit)")
    ap.add_argument("--include-media", action="store_true",
                    help="also download zip/pdf/midi media files")
    ap.add_argument("--refresh", action="store_true",
                    help="re-check already-snapshotted pages with conditional GETs")
    args = ap.parse_args()
    Crawler(operas.get(args.opera), args.delay, args.max_pages,
            args.include_media, args.refresh).run()


if __name__ == "__main__":
    main()
