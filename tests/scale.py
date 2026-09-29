"""Seeded data for the performance gates: in-memory cases, and a full dataset on disk."""

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import generate

from detecttrace.checklist import ArgRule, Checklist, ChecklistItem
from detecttrace.config import normalize_label
from detecttrace.model import Case, ToolCall, Verdict

_CLASSES = ("impossible_travel", "oauth_consent")
_WEEK_COUNT = 6
_FIRST_MONDAY = datetime(2026, 8, 3, tzinfo=UTC)
_HOUR_NS = 3_600 * 1_000_000_000
_WEEK_HOURS = 7 * 24
_DOCUMENTATION_NETWORKS = ("192.0.2", "198.51.100", "203.0.113")
_FILLER_TOOLS = ("get_asset", "get_ticket", "notify_analyst")

_ITEMS = (
    ChecklistItem(
        id="login_history", tool="search_logs", args={"range": ArgRule(min_duration="24h")}
    ),
    ChecklistItem(
        id="event_window",
        tool="get_events",
        args={"$": ArgRule(min_duration="24h", start="start_time", end="end_time")},
    ),
    ChecklistItem(id="kql_lookback", tool="run_kql", args={"query": ArgRule(kql_min_ago="24h")}),
    ChecklistItem(
        id="lookback_hours", tool="get_history", args={"lookback_hours": ArgRule(min=24)}
    ),
    ChecklistItem(id="user_lookup", tool="lookup_user"),
)

SCALE_CHECKLISTS: dict[str, Checklist] = {
    normalize_label(name): Checklist(alert_class=name, items=_ITEMS) for name in _CLASSES
}


# Repeats of existing tools, so a case makes 6-12 calls like a busy triage agent, not the
# demo's 5-7; the checklists stay the demo's.
_EXTRA_TOOLS = {
    "impossible_travel": ("query_sentinel", "get_signin_logs", "get_ip_reputation"),
    "oauth_consent": (
        "get_user_profile",
        "query_sentinel",
        "get_consent_events",
        "get_oauth_grants",
    ),
}


def write_scale_dataset(folder: Path, count: int = 50_000) -> Path:
    """Write about `count` demo-like cases as gzip Collector files; return the config path."""
    demo = generate.load_scenario("demo")
    cases_per_week = -(-count // (len(demo.classes) * demo.weeks))
    scenario = demo.model_copy(
        update={
            "classes": tuple(
                spec.model_copy(
                    update={
                        "cases_per_week": cases_per_week,
                        "tools": spec.tools + _EXTRA_TOOLS[spec.name],
                    }
                )
                for spec in demo.classes
            ),
            # 50 cases per line keeps every line far below the reader's per-line cap.
            "traces": generate.TraceLayout(files=8, cases_per_batch=50),
            "noise": demo.noise.model_copy(update={"failure": 0.1}),
        }
    )
    cases = generate.make_cases(scenario)
    generate.write_traces(scenario, cases, folder / "traces")
    generate.write_verdicts(cases, folder / "verdicts.csv")
    generate.write_checklists(scenario, folder / "checklists")
    return generate.write_text(folder / "detecttrace.yaml", generate.to_yaml(scenario.config))


def make_scale_cases(count: int = 50_000, seed: int = 7) -> list[Case]:
    rng = random.Random(seed)
    return [_make_case(rng, index) for index in range(count)]


def _make_case(rng: random.Random, index: int) -> Case:
    week = rng.randrange(_WEEK_COUNT)
    start_ns = (
        int(
            (
                _FIRST_MONDAY + timedelta(hours=week * _WEEK_HOURS + rng.randrange(_WEEK_HOURS))
            ).timestamp()
        )
        * 1_000_000_000
    )
    is_unversioned = rng.random() < 0.01
    version = None if is_unversioned else ("v1" if week < 2 else "v2")
    analyst = _pick_analyst(rng)
    agent = analyst if rng.random() < 0.85 else rng.choice(list(Verdict))
    if rng.random() < 0.01:
        analyst = None
    if rng.random() < 0.01:
        agent = None
    return Case(
        case_id=f"case-{index:06d}",
        alert_class=_CLASSES[index % len(_CLASSES)],
        prompt_version=version,
        analyst_verdict=analyst,
        agent_verdict=agent,
        start_ns=start_ns,
        tool_calls=_make_tool_calls(rng, index, start_ns),
        is_incomplete_trace=False,
    )


def _pick_analyst(rng: random.Random) -> Verdict:
    if rng.random() < 0.15:
        return Verdict.TRUE_POSITIVE
    return rng.choice((Verdict.FALSE_POSITIVE, Verdict.BENIGN))


def _make_tool_calls(rng: random.Random, index: int, case_start_ns: int) -> tuple[ToolCall, ...]:
    tools = [item.tool for item in _ITEMS if rng.random() < 0.9]
    tools += rng.choices(_FILLER_TOOLS, k=max(0, rng.randint(6, 12) - len(tools)))
    calls = []
    for position, tool in enumerate(tools):
        start_ns = case_start_ns + (position + 1) * _HOUR_NS // 60
        calls.append(
            ToolCall(
                span_id=f"{index * 16 + position + 1:016x}",
                tool_name=tool,
                arguments=_make_arguments(rng, tool),
                start_ns=start_ns,
                end_ns=start_ns + _HOUR_NS // 120,
                is_failed=rng.random() < 0.03,
            )
        )
    return tuple(calls)


def _make_arguments(rng: random.Random, tool: str) -> str:
    user = f"user{rng.randrange(500)}@example.com"
    if tool == "search_logs":
        return json.dumps({"user": user, "range": rng.choice(("24h", "-7d", "12h", "1h", "PT48H"))})
    if tool == "get_events":
        start = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=rng.randrange(_WEEK_HOURS * 6))
        end = start + timedelta(hours=rng.choice((2, 24, 36, 72)))
        return json.dumps(
            {
                "start_time": start.isoformat().replace("+00:00", "Z"),
                "end_time": end.isoformat().replace("+00:00", "Z"),
            }
        )
    if tool == "run_kql":
        window = rng.choice(("1d", "7d", "6h", "30m", "72h"))
        return json.dumps({"query": f"SignInLogs | where TimeGenerated > ago({window}) | take 50"})
    if tool == "get_history":
        return json.dumps({"user": user, "lookback_hours": rng.choice((6, 24, 72, 168))})
    if tool == "lookup_user":
        return json.dumps({"user": user})
    network = rng.choice(_DOCUMENTATION_NETWORKS)
    return json.dumps({"ip": f"{network}.{rng.randrange(1, 255)}", "user": user})
