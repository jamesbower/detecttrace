"""Read durations, KQL lookbacks, and timestamp differences for checklist argument rules."""

import re
from datetime import datetime, timedelta

# [0-9] rather than \d: \d also matches non-ASCII digits, which float() would accept.
_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_SHORT = re.compile(_NUMBER + r"(s|m|h|d)")
# Years and months have no fixed length, so ISO 8601 durations stop at weeks.
_ISO = re.compile(
    rf"P(?:{_NUMBER}W)?(?:{_NUMBER}D)?(?:T(?:{_NUMBER}H)?(?:{_NUMBER}M)?(?:{_NUMBER}S)?)?"
)
# One nested bracket level is allowed so ago(time(1d)) is reported rather than missed.
_KQL_AGO = re.compile(r"\bago\(\s*((?:[^()]|\([^()]*\))*?)\s*\)")
_KQL_SPAN = re.compile(_NUMBER + r"(ms|d|h|m|s)")
_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta | None:
    # A leading minus is ignored: "-24h" is how many tools write a 24-hour lookback.
    text = text.strip().removeprefix("-")
    try:
        if match := _SHORT.fullmatch(text):
            return timedelta(seconds=float(match[1]) * _SECONDS[match[2]])
        match = _ISO.fullmatch(text)
        if match is None or text.endswith("T") or all(group is None for group in match.groups()):
            return None
        weeks, days, hours, minutes, seconds = (float(group or 0) for group in match.groups())
        return timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    except OverflowError:
        return None


def kql_lookbacks(text: str) -> tuple[list[timedelta], list[str]]:
    readable: list[timedelta] = []
    unreadable: list[str] = []
    for raw in _KQL_AGO.findall(text):
        match = _KQL_SPAN.fullmatch(raw)
        if match is None:
            unreadable.append(raw)
            continue
        try:
            readable.append(timedelta(seconds=float(match[1]) * _SECONDS[match[2]]))
        except OverflowError:
            unreadable.append(raw)
    return readable, unreadable


def timestamp_difference(start: str, end: str) -> timedelta | None:
    try:
        start_time = datetime.fromisoformat(start)
        end_time = datetime.fromisoformat(end)
    except ValueError:
        return None
    # Subtracting a naive time from an aware one has no meaning; don't guess a time zone.
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        return None
    return abs(end_time - start_time)
