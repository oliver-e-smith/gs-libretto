"""Registry of the fourteen G&S operas and their gsarchive.net section paths.

Path slugs verified against the live site are marked verified=True; the rest
are best guesses that the crawler verifies (and reports) before fetching.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Opera:
    slug: str            # our canonical slug (used in DB and URLs)
    title: str
    year: int
    site_path: str       # path prefix on gsarchive.net, e.g. "pirates"
    verified: bool = False
    # Pages that seed the crawl for this opera, relative to the site root.
    # The crawler also follows same-section links found in these pages.
    seeds: tuple = field(default_factory=tuple)


OPERAS = [
    Opera("thespis",    "Thespis",                          1871, "thespis"),
    Opera("trial",      "Trial by Jury",                    1875, "trial"),
    Opera("sorcerer",   "The Sorcerer",                     1877, "sorcerer"),
    Opera("pinafore",   "H.M.S. Pinafore",                  1878, "pinafore"),
    Opera("pirates",    "The Pirates of Penzance",          1879, "pirates", verified=True,
          seeds=("pirates/html/index.html", "pirates/web_op/operhome.html")),
    Opera("patience",   "Patience",                         1881, "patience"),
    Opera("iolanthe",   "Iolanthe",                         1882, "iolanthe"),
    Opera("ida",        "Princess Ida",                     1884, "princess_ida"),
    Opera("mikado",     "The Mikado",                       1885, "mikado"),
    Opera("ruddigore",  "Ruddigore",                        1887, "ruddigore"),
    Opera("yeomen",     "The Yeomen of the Guard",          1888, "yeomen"),
    Opera("gondoliers", "The Gondoliers",                   1889, "gondoliers"),
    Opera("utopia",     "Utopia, Limited",                  1893, "utopia"),
    Opera("grandduke",  "The Grand Duke",                   1896, "grand_duke"),
]

BY_SLUG = {o.slug: o for o in OPERAS}


def get(slug: str) -> Opera:
    try:
        return BY_SLUG[slug]
    except KeyError:
        raise SystemExit(
            f"Unknown opera slug {slug!r}. Known: {', '.join(BY_SLUG)}") from None
