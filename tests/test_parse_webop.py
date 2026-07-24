"""Parser tests against fixtures replicating the Archive's markup styles.

The fixtures mirror conventions observed on the live Pirates pages
(label-column tables, parallel-column tables, inline bold dialogue speakers,
italic directions, nav/MIDI furniture). When the real snapshot lands, add
regression tests against actual pages alongside these.
"""

from pathlib import Path

import pytest

from gsdb.parse_webop import OperaConfig, parse_page

FIX = Path(__file__).parent / "fixtures"
CFG = OperaConfig("pirates")


def load(name, filename):
    return parse_page((FIX / name).read_bytes(), filename, CFG)


def all_voices(parsed):
    return [v for b in parsed.blocks for v in b.voices]


class TestNumberPage:
    @pytest.fixture(scope="class")
    def page(self):
        return load("pirates01_style.html", "pirates01.html")

    def test_heading(self, page):
        assert page.kind == "number"
        assert page.number_label.lower().startswith("no. 1")
        assert "pirate sherry" in (page.title or "").lower()

    def test_scene_direction_flagged(self, page):
        first_sung = next(b for b in page.blocks if b.kind == "sung")
        directions = [b for b in page.blocks if b.kind == "direction"]
        assert any("rocky sea-shore" in v.lines[0].text
                   for b in directions for v in b.voices)
        # direction is not inside sung searchable text
        assert "rocky" not in first_sung.voices[0].sung_text.lower()

    def test_label_column_voices(self, page):
        attrs = [v.attribution for v in all_voices(page) if v.attribution]
        assert "Chorus" in attrs and "Samuel" in attrs and "All" in attrs

    def test_lines_in_order(self, page):
        chorus = next(v for v in all_voices(page) if v.attribution == "Chorus")
        assert chorus.lines[0].text.startswith("Pour, oh pour")
        assert chorus.lines[3].text.startswith("Let the pirate bumper")

    def test_nav_furniture_dropped(self, page):
        text = " ".join(l.text for v in all_voices(page) for l in v.lines)
        assert "Archive Home" not in text
        assert "MIDI" not in text

    def test_inline_direction_kept_flagged(self, page):
        dirs = [l for v in all_voices(page) for l in v.lines if l.is_direction]
        assert any("FREDERIC rises" in l.text for l in dirs)


class TestParallelPage:
    @pytest.fixture(scope="class")
    def page(self):
        return load("pirates09_style.html", "pirates09.html")

    def test_parallel_block_exists(self, page):
        parallel = [b for b in page.blocks if len(b.voices) >= 2]
        assert parallel, "no parallel block found"

    def test_voices_not_merged(self, page):
        block = next(b for b in page.blocks if len(b.voices) >= 2)
        assert len(block.voices) == 2
        left, right = block.voices
        assert left.attribution == "Mabel"
        assert right.attribution == "Girls"
        assert "Did ever maiden wake" in left.sung_text
        assert "beautifully blue" in right.sung_text
        # cross-contamination check
        assert "beautifully blue" not in left.sung_text
        assert "Did ever maiden" not in right.sung_text

    def test_singer_change_across_rows(self, page):
        """Row 2 changes column 1's singer to Frederic -> a new parallel
        block, with column 2 inheriting 'Girls'. Sequence preserved."""
        parallel = [b for b in page.blocks if len(b.voices) >= 2]
        assert len(parallel) >= 2
        first, second = parallel[0], parallel[1]
        assert first.voices[0].attribution == "Mabel"
        assert second.voices[0].attribution == "Frederic"
        assert "pirate loathed" in second.voices[0].sung_text
        # inherited attribution for the continuing chorus column
        assert second.voices[1].attribution == "Girls"
        assert "rained but yesterday" in second.voices[1].sung_text

    def test_take_heart_attributed_to_mabel(self, page):
        mabels = [v for v in all_voices(page) if v.attribution == "Mabel"]
        assert any("take any heart but ours" in v.sung_text.lower()
                   for v in mabels)

    def test_anchor_captured(self, page):
        anchored = [b for b in page.blocks if b.source_anchor]
        assert any(b.source_anchor == "No10" for b in anchored)


class TestDialoguePage:
    @pytest.fixture(scope="class")
    def page(self):
        return load("pirates05d_style.html", "pirates05d.html")

    def test_kind_and_label(self, page):
        assert page.kind == "dialogue"
        assert "no. 5" in page.number_label.lower()

    def test_speakers_split(self, page):
        attrs = [v.attribution for v in all_voices(page) if v.attribution]
        assert attrs[:2] == ["Kate", "Edith"]

    def test_speech_text(self, page):
        kate = next(v for v in all_voices(page) if v.attribution == "Kate")
        assert kate.lines[0].text.startswith("What a picturesque spot!")
        assert not kate.lines[0].is_direction

    def test_blocks_are_spoken(self, page):
        kinds = {b.kind for b in page.blocks if b.voices[0].attribution}
        assert kinds <= {"spoken"}

    def test_standalone_direction(self, page):
        dirs = [b for b in page.blocks if b.kind == "direction"]
        assert any("carry out the suggestion" in v.lines[0].text
                   for b in dirs for v in b.voices)
