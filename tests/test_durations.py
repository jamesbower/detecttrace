from datetime import timedelta

import pytest

from detecttrace.durations import kql_lookbacks, parse_duration, timestamp_difference

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("24h", 24 * HOUR),
        ("90m", timedelta(minutes=90)),
        ("7d", 7 * DAY),
        ("30s", timedelta(seconds=30)),
        ("1.5d", 36 * HOUR),
        ("-24h", 24 * HOUR),
        (" 24h ", 24 * HOUR),
        ("P1D", DAY),
        ("PT24H", 24 * HOUR),
        ("P1W", 7 * DAY),
        ("P1DT12H", 36 * HOUR),
        ("PT1H30M", timedelta(minutes=90)),
        ("PT0.5S", timedelta(seconds=0.5)),
        ("-P1D", DAY),
    ],
)
def test_parse_duration_reads_short_and_iso_forms(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "24",
        "24 h",
        "24H",
        "1y",
        "P1Y",
        "P1M",
        "P",
        "PT",
        "P1DT",
        "--24h",
        "24h30m",
        "1e3h",
        "99999999999999d",
        "9" * 10000 + "h",
        "\uff12\uff14h",
        "P\uff12D",
    ],
)
def test_parse_duration_rejects_unreadable_text(text):
    assert parse_duration(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SigninLogs | where TimeGenerated > ago(24h)", [24 * HOUR]),
        ("ago(1d)", [DAY]),
        ("ago(1.5d)", [36 * HOUR]),
        ("ago(90m)", [timedelta(minutes=90)]),
        ("ago(30s)", [timedelta(seconds=30)]),
        ("ago(500ms)", [timedelta(milliseconds=500)]),
        ("ago( 7d )", [7 * DAY]),
        ("ago(1d) and ago(2h)", [DAY, 2 * HOUR]),
    ],
)
def test_kql_lookbacks_reads_lookbacks(text, expected):
    assert kql_lookbacks(text) == (expected, [])


@pytest.mark.parametrize(
    ("text", "raw"),
    [
        ("ago(1day)", "1day"),
        ("ago(time(1d))", "time(1d)"),
        ("ago(-1d)", "-1d"),
        ("ago()", ""),
        ("ago(\uff11d)", "\uff11d"),
        ("ago(99999999999999d)", "99999999999999d"),
    ],
)
def test_kql_lookbacks_reports_unreadable_spans(text, raw):
    assert kql_lookbacks(text) == ([], [raw])


@pytest.mark.parametrize("text", ["timeago(1d)", "no lookback here", ""])
def test_kql_lookbacks_ignores_text_without_a_lookback(text):
    assert kql_lookbacks(text) == ([], [])


def test_kql_lookbacks_handles_many_occurrences():
    readable, unreadable = kql_lookbacks("ago(1d) " * 10000)

    assert (len(readable), unreadable) == (10000, [])


def test_kql_lookbacks_handles_unclosed_brackets():
    assert kql_lookbacks("ago(" * 10000) == ([], [])


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", DAY),
        ("2026-09-02T00:00:00Z", "2026-09-01T00:00:00Z", DAY),
        ("2026-09-01T00:00:00+02:00", "2026-09-01T00:00:00Z", 2 * HOUR),
        ("2026-09-01T00:00:00", "2026-09-01T06:00:00", 6 * HOUR),
    ],
)
def test_timestamp_difference_is_absolute(start, end, expected):
    assert timestamp_difference(start, end) == expected


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-09-01T00:00:00", "2026-09-01T00:00:00Z"),
        ("2026-09-01T00:00:00Z", "2026-09-01T00:00:00"),
        ("yesterday", "2026-09-01T00:00:00Z"),
        ("2026-09-01T00:00:00Z", "yesterday"),
        ("", ""),
    ],
)
def test_timestamp_difference_rejects_unreadable_input(start, end):
    assert timestamp_difference(start, end) is None
