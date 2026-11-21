"""Hearing song requests right: names she knows, fixed by sound (music_words.py)."""

import pytest

from coco.voice.music_words import MusicWords, music_pick, sound_key


@pytest.fixture
def words(tmp_path):
    w = MusicWords(tmp_path / "music_words.json", builtin=["Arijit Singh", "Seedhe Maut", "King"])
    w.learn_played('Kesariya (From "Brahmastra") by Pritam, Arijit Singh')
    w.learn_played("Madira by Seedhe Maut")
    return w


def test_sound_key_ignores_hindi_spellings():
    assert sound_key("Seedhe Maut") == sound_key("Sidhe Mot")
    assert sound_key("Kesariya") == sound_key("Kesaria")


def test_learns_what_she_played(words, tmp_path):
    assert words.learned == ["Kesariya", "Pritam", "Arijit Singh", "Madira", "Seedhe Maut"]
    again = MusicWords(tmp_path / "music_words.json", builtin=[])
    assert "Madira" in again.names  # (kept on disk)


def test_fixes_misheard_names(words):
    assert words.fix_request("Le madira by sidhe maut.")[0] == "play Madira by Seedhe Maut"
    assert words.fix_request("play case area by Arjun Singh.")[0] == "play Kesariya by Arijit Singh"
    fixed, sure = words.fix_request("Play Madira on your book")
    assert fixed == "play Madira on YouTube" and sure > 0.9
    assert words.fix_request("Seedhe Maut ke gaane lagao")[0] == "play Seedhe Maut"
    assert words.fix_request("what is a capacitor") is None  # not a song request
    assert words.fix_request("play some notepad") is None  # nothing she knows


@pytest.mark.parametrize(
    ("english", "hinglish", "want"),
    [
        ("Play with me.", "Le madira by sidhe maut.", "play Madira by Seedhe Maut"),
        ("play case area by Arjun Singh.", "Le ke shariya by arjit sinh.",
         "play Kesariya by Arijit Singh"),
        ("Play with me.", "Lekin shariya.", "play Kesariya"),  # English copied its hint
        ("Play my light songs.", "My light songs.", "play my liked songs"),
        ("Play with me.", "Raat lambiya.", "play Raat lambiya"),  # unknown: Spotify searches
        # never music:
        ("Play with me.", "Jay with me.", None),  # her play command
        ("Play with me.", "Idhar aao.", None),  # a cat command in Hinglish
        ("What is a capacitor?", "Capacitor kya hai?", None),
        ("The king is here.", "King yahan hai.", None),  # a known name, but no "play"
    ],
)  # fmt: skip
def test_music_pick(words, english, hinglish, want):
    assert music_pick(english, hinglish, words) == want


def test_your_own_names_from_the_dashboard(tmp_path):
    w = MusicWords(tmp_path / "m.json", builtin=[])
    assert w.fix_request("play namaste you tea") is None
    MusicWords(tmp_path / "m.json", builtin=[]).set_mine(["Namastute"])  # (the dashboard)
    assert w.fix_request("play namastu tee")[0] == "play Namastute"  # picked up live
