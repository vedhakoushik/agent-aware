"""City -> IATA resolution.

Regression tests for the bug where _iata() fell back to "first three letters,
uppercased" for any city outside its 19-entry table. That guess was wrong far
more often than right AND silent: "Bhubaneswar" became "BHU" (Bhatinda), so the
pipeline returned confident Google Flights fares for a city the user never asked
about.

Offline — no API keys, no network.
"""
import pytest

from backend.nodes.search import (
    _airport_table,
    _flight_base_params,
    _iata,
    unresolved_flight_cities,
)


# ── the mapped happy path ────────────────────────────────────────────

@pytest.mark.parametrize("city,code", [
    ("Bangalore", "BLR"),
    ("bengaluru", "BLR"),
    ("Mumbai", "BOM"),
    ("Bombay", "BOM"),
    ("New Delhi", "DEL"),
    ("Dubai", "DXB"),
])
def test_known_cities_resolve(city, code):
    assert _iata(city) == code


def test_lookup_ignores_case_and_surrounding_whitespace():
    assert _iata("  bAnGaLoRe  ") == "BLR"


# ── the bug: never fabricate a code ──────────────────────────────────

@pytest.mark.parametrize("city", [
    "Bhubaneswar",      # used to yield BHU — that is Bhatinda, a different city
    "Varanasi",         # used to yield VAR — not a valid IATA code at all
    "Trivandrum",       # used to yield TRI — Tri-Cities, USA
    "Kozhikode",        # used to yield KOZ — not a valid IATA code
])
def test_previously_fabricated_codes_are_now_real_or_none(city):
    """These four all have real airports, so after widening the table they must
    resolve correctly — and must never resolve to the old three-letter guess."""
    resolved = _iata(city)
    assert resolved is not None, f"{city} should be in config/airports.yaml"
    assert resolved != city[:3].upper(), (
        f"{city} resolved to the old first-three-letters guess"
    )


@pytest.mark.parametrize("city,expected", [
    ("Bhubaneswar", "BBI"),
    ("Varanasi", "VNS"),
    ("Trivandrum", "TRV"),
    ("Kozhikode", "CCJ"),
    ("Calicut", "CCJ"),
])
def test_widened_table_has_the_right_codes(city, expected):
    assert _iata(city) == expected


def test_genuinely_unknown_city_returns_none_rather_than_guessing():
    assert _iata("Narnia") is None
    assert _iata("Some Place That Has No Airport") is None


def test_empty_and_whitespace_input_returns_none():
    assert _iata("") is None
    assert _iata("   ") is None
    assert _iata(None) is None


# ── airport codes typed directly ─────────────────────────────────────

def test_a_known_airport_code_passes_through():
    assert _iata("BLR") == "BLR"
    assert _iata("blr") == "BLR"


def test_an_unknown_three_letter_string_is_not_treated_as_a_code():
    """The whole point: three letters is not evidence of an airport."""
    assert _iata("ZZZ") is None
    assert _iata("XXX") is None


def test_city_names_win_over_code_shaped_matching():
    """'Goa' is three letters AND a city name — it must resolve as the city."""
    assert _iata("Goa") == "GOI"


# ── callers must refuse to build a query from an unresolved city ─────

def test_flight_base_params_returns_none_when_an_endpoint_is_unresolved():
    assert _flight_base_params({"origin": "Bangalore", "destination": "Narnia",
                                "date": "2026-12-01"}) is None
    assert _flight_base_params({"origin": "Narnia", "destination": "Delhi",
                                "date": "2026-12-01"}) is None


def test_flight_base_params_builds_a_query_when_both_resolve():
    base = _flight_base_params({"origin": "Bangalore", "destination": "Delhi",
                                "date": "2026-12-01"})
    assert base is not None
    assert base["departure_id"] == "BLR"
    assert base["arrival_id"] == "DEL"
    assert base["outbound_date"] == "2026-12-01"


def test_unresolved_flight_cities_names_the_offending_input():
    assert unresolved_flight_cities({"origin": "Bangalore", "destination": "Delhi"}) == []
    assert unresolved_flight_cities(
        {"origin": "Narnia", "destination": "Delhi"}) == ["Narnia"]
    assert unresolved_flight_cities(
        {"origin": "Narnia", "destination": "Atlantis"}) == ["Narnia", "Atlantis"]


# ── the data file itself ─────────────────────────────────────────────

def test_airport_table_loads_from_yaml_and_is_substantially_wider_than_the_old_table():
    table = _airport_table()
    assert len(table) > 100, "config/airports.yaml should cover far more than the old 19"


def test_every_code_in_the_table_is_a_plausible_iata_code():
    bad = {city: code for city, code in _airport_table().items()
           if not (len(code) == 3 and code.isalpha() and code.isupper())}
    assert not bad, f"malformed IATA codes in config/airports.yaml: {bad}"
