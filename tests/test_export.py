"""Subtitle export tests, reusing the integration fixture DB."""

from gsdb.export import section_cues, to_srt, to_text
from tests.test_build_integration import built_db  # noqa: F401  (fixture)


def test_simultaneous_cues_grouped(built_db):  # noqa: F811
    cues = section_cues(built_db, "pirates", 3)  # Nos. 9 & 10 fixture
    sim = [c for c in cues if c.get("simultaneous")]
    assert sim, "no simultaneous cues"
    first = sim[0]
    attrs = {v["attribution"] for v in first["voices"]}
    assert {"Mabel", "Girls"} <= attrs
    assert any("maiden wake" in v["line"] for v in first["voices"])


def test_character_filter(built_db):  # noqa: F811
    cues = section_cues(built_db, "pirates", 3, character="Mabel")
    assert cues
    for c in cues:
        for v in c["voices"]:
            assert "mabel" in v["attribution"].lower()


def test_directions_excluded(built_db):  # noqa: F811
    cues = section_cues(built_db, "pirates", 1)
    text = " ".join(v["line"] for c in cues for v in c["voices"])
    assert "rocky sea-shore" not in text
    assert "FREDERIC rises" not in text


def test_srt_format(built_db):  # noqa: F811
    srt = to_srt(section_cues(built_db, "pirates", 1))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:03,500\n")
    assert "Pour, oh pour, the pirate sherry;" in srt


def test_text_marks_together(built_db):  # noqa: F811
    txt = to_text(section_cues(built_db, "pirates", 3))
    assert "* TOGETHER" in txt
    assert "||" in txt
