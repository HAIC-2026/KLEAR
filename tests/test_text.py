# Tests for Hawaiian place-name spelling.
import unicodedata

import pytest

from klear.text import OKINA, OKINA_LOOKALIKES, hawaiian, misspellings


def test_hawaiian_fixes_names():
    assert hawaiian("Kauai County") == "Kauaʻi County"
    assert hawaiian("Hawai'i") == "Hawaiʻi"
    assert hawaiian("KAHULUI") == "KAHULUI"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("Hawaii County", "Hawaiʻi County"),
        ("Hawai’i", "Hawaiʻi"),
        ("Oahu", "Oʻahu"),
        ("O'ahu", "Oʻahu"),
        ("Lanai", "Lānaʻi"),
        ("Molokai", "Molokaʻi"),
        ("Kahoolawe", "Kahoʻolawe"),
        ("Niihau", "Niʻihau"),
        ("Hawaiʻi", "Hawaiʻi"),
        ("Hawaiian", "Hawaiian"),
        ("Kauaiana", "Kauaiana"),
        ("HAWAII", "HAWAII"),
    ],
)
def test_hawaiian_mapping(raw, want):
    assert hawaiian(raw) == want


@pytest.mark.parametrize("mark", OKINA_LOOKALIKES)
def test_hawaiian_reads_every_okina_lookalike(mark):
    assert hawaiian(f"Hawai{mark}i and Kane{mark}ohe") == "Hawaiʻi and Kāneʻohe"
    assert misspellings(f"Kāne{mark}ohe")  # flagged, though a ` is read as Markdown and dropped first


def test_okina_lookalikes_are_distinct_and_exclude_the_okina():
    assert len(set(OKINA_LOOKALIKES)) == len(OKINA_LOOKALIKES) and OKINA not in OKINA_LOOKALIKES
    assert {"'", "`", "‘", "’", "ʼ"} <= set(OKINA_LOOKALIKES)


def test_hawaiian_normalizes_nfc():
    out = hawaiian(unicodedata.normalize("NFD", "Lānaʻi"))
    assert out == "Lānaʻi"
    assert unicodedata.is_normalized("NFC", out)
    out = hawaiian("La\u0304na'i")
    assert out == "Lānaʻi"
    assert unicodedata.is_normalized("NFC", out)


def test_hawaiian_idempotent():
    s = hawaiian("Hawaii and Kauai and Oahu")
    assert hawaiian(s) == s == "Hawaiʻi and Kauaʻi and Oʻahu"


def test_site_names_get_okina():
    assert hawaiian("West O`ahu Community Health") == "West Oʻahu Community Health"
    assert hawaiian("Waimanalo Health Center") == "Waimanalo Health Center"
    assert hawaiian("Hale Ola `Akolu-Luluku") == "Hale Ola `Akolu-Luluku"
    assert hawaiian("Kaua`i Community Health, O'ahu Clinic") == "Kauaʻi Community Health, Oʻahu Clinic"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("Kaneohe", "Kāneʻohe"),
        ("Waianae", "Waiʻanae"),
        ("Waimanalo", "Waimānalo"),
        ("Hawaii Kai", "Hawaiʻi Kai"),
        ("Ewa Beach", "ʻEwa Beach"),
        ("'Ewa Beach", "ʻEwa Beach"),
        ("Kailua-Kona", "Kailua-Kona"),
        ("Lihue", "Līhuʻe"),
        ("Hilo and Lahaina and Kahului", "Hilo and Lahaina and Kahului"),
        ("Kāneʻohe", "Kāneʻohe"),
    ],
)
def test_hawaiian_fixes_place_names(raw, want):
    assert hawaiian(raw) == want


def test_org_names_kept_exactly():
    assert (
        hawaiian("Waianae Coast Comprehensive Health Center") == "Waianae Coast Comprehensive Health Center"
    )
    assert hawaiian("Waianae Vision Care in Waianae") == "Waianae Vision Care in Waiʻanae"
    assert hawaiian("KANEOHE ELDERLY") == "KANEOHE ELDERLY"


def test_misspellings_any_case():
    from klear.text import misspellings

    assert sorted(misspellings("kaneohe, OAHU and Waianae")) == ["OAHU", "Waianae", "kaneohe"]
    assert misspellings("Kāneʻohe KĀNEʻOHE Hilo hilo") == []
    assert misspellings("Waimanalo Health Center") == []


def test_web_addresses_are_not_place_names():
    text = (
        "Apply at https://medquest.hawaii.gov/en/apply, call hpha.hawaii.gov/how-to-apply, or see HAWAII.GOV."
    )
    assert misspellings(text) == []
    assert hawaiian(text) == text
    # The place name beside a link is still checked and fixed.
    assert misspellings("Hawaii residents: health.hawaii.gov") == ["Hawaii"]
    assert hawaiian("Hawaii residents: health.hawaii.gov") == "Hawaiʻi residents: health.hawaii.gov"
    assert misspellings("Lihue.") == ["Lihue"]


def test_misspellings_ignores_invisible_characters():
    from klear.text import misspellings

    assert misspellings("Kane\u200bohe") == ["Kaneohe"]


def test_misspellings_ignores_emphasis_markers():
    from klear.text import misspellings

    assert misspellings("_Kaneohe_ and __Waianae__") == ["Kaneohe", "Waianae"]


@pytest.mark.parametrize(
    "mark",
    [
        "\u034f",
        "\u115f",
        "\u1160",
        "\u3164",
        "\uffa0",
        "\u180b",
        "\u180f",
        "\ufe00",
        "\ufe0f",
        "\U000e0100",
        "\U000e01ef",
        "\u17b4",
        "\u17b5",
        "\u200b",
        "\u200f",
        "\u202a",
        "\u202e",
        "\u2060",
        "\u2065",
        "\u206f",
        "\ufeff",
        "\ufff0",
        "\ufff8",
        "\U0001bca0",
        "\U0001bca3",
        "\U0001d173",
        "\U0001d17a",
        "\U000e0000",
        "\U000e0fff",
    ],
)
def test_misspellings_ignores_default_ignorables(mark):
    from klear.text import misspellings

    assert misspellings(f"Kane{mark}ohe") == ["Kaneohe"]


def test_strip_ignorable():
    from klear.text import strip_ignorable

    assert strip_ignorable("a\u034fb\u200bc\ufe0fd\U000e0100e") == "abcde"
    assert strip_ignorable("Kāneʻohe") == "Kāneʻohe"


def test_misspellings_recomposes_after_stripping():
    from klear.text import misspellings

    assert misspellings("Ka\u034f\u0304neohe") == ["Kāneohe"]


def test_strip_ignorable_keeps_neighbors():
    from klear.text import strip_ignorable

    kept = "\u17b3\u17b6\U0001bc9f\U0001bca4\U0001d172\U000e1000"
    assert strip_ignorable(kept) == kept
    assert strip_ignorable("a\u2065b\U000e0fffc") == "abc"
