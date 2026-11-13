"""
Builds the *would-be* itinerary cache key for a trip. Used for logging only:
no cache exists yet. Free-text preferences are deliberately NOT part of the
key (they would almost never match and may contain private details); we only
record whether any were given.
"""
import re

from .ai import PROMPT_VERSION

SCHEMA_VERSION = "text-v0"   # bump when itineraries become structured JSON


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def duration_bucket(days: int) -> str:
    if days <= 2:
        return "1-2d"
    if days <= 4:
        return "3-4d"
    if days <= 7:
        return "5-7d"
    if days <= 10:
        return "8-10d"
    return "11d+"


def budget_bucket(per_person_per_day: float) -> str:
    for limit, label in ((1500, "<1.5k"), (3000, "1.5-3k"), (5000, "3-5k"), (8000, "5-8k")):
        if per_person_per_day < limit:
            return label
    return "8k+"


def season(start_date) -> str:
    if not start_date:
        return "any"
    m = start_date.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "summer"
    if m in (6, 7, 8, 9):
        return "monsoon"
    return "autumn"


def group_type(num_people: int) -> str:
    if num_people <= 1:
        return "solo"
    if num_people == 2:
        return "pair"
    if num_people <= 5:
        return "small"
    return "large"


def build_cache_key(trip) -> str:
    days = max(trip.days or 1, 1)
    people = max(trip.num_people or 1, 1)
    per_person_per_day = trip.total_budget() / people / days
    return "|".join([
        _norm(trip.destination),
        duration_bucket(days),
        season(trip.start_date),
        budget_bucket(per_person_per_day),
        _norm(trip.travel_type),
        _norm(trip.travel_mode),
        group_type(people),
        "prefs" if (trip.additional_preferences or "").strip() else "noprefs",
        PROMPT_VERSION,
        SCHEMA_VERSION,
    ])