"""Decide, for each case, which checklist items its tool calls satisfied."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from functools import cache
from typing import TypeGuard

from detecttrace.checklist import ArgRule, Checklist, ChecklistItem, parse_path
from detecttrace.config import normalize_label
from detecttrace.durations import kql_lookbacks, parse_duration, timestamp_difference
from detecttrace.model import Case, Issue, IssueKind, ToolCall

# Argument values can be sensitive, so details show at most this much of one.
_MAX_SHOWN = 80
# A path with no value. Distinct from None, because JSON null is a value.
_MISSING = object()
_UNREADABLE = object()


class ItemStatus(StrEnum):
    SATISFIED = "satisfied"
    FAILED = "failed"
    MISSED = "missed"


class MissedReason(StrEnum):
    NOT_CALLED = "not_called"
    WRONG_ARGUMENTS = "wrong_arguments"  # it ran, but its arguments didn't pass or couldn't be read


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    status: ItemStatus
    missed_reason: MissedReason | None  # set only when status is MISSED


@dataclass(frozen=True, slots=True)
class CaseEvidence:
    case_id: str
    outcomes: tuple[ItemOutcome, ...]  # checklist item order

    @property
    def satisfied_count(self) -> int:
        return sum(outcome.status is ItemStatus.SATISFIED for outcome in self.outcomes)


def json_equal(left: object, right: object) -> bool:
    """Compare two JSON values deeply by JSON type: 24 equals 24.0, but true never equals 1."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(json_equal, left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            json_equal(value, right[key]) for key, value in left.items()
        )
    return False


def evaluate_case(case: Case, checklist: Checklist, issues: list[Issue]) -> CaseEvidence:
    """Give each checklist item a status for this case; unreadable input is appended to `issues`."""
    calls_by_tool: dict[str, list[ToolCall]] = {}
    for call in case.tool_calls:
        calls_by_tool.setdefault(call.tool_name, []).append(call)
    arguments_by_span: dict[str, object] = {}
    outcomes = tuple(
        _evaluate_item(
            case.case_id, item, calls_by_tool.get(item.tool, []), arguments_by_span, issues
        )
        for item in checklist.items
    )
    return CaseEvidence(case.case_id, outcomes)


def find_rule_type_mismatches(
    cases: Sequence[Case], checklists: Mapping[str, Checklist]
) -> list[Issue]:
    """Warn once per checklist item whose numeric or duration rule meets the other kind of value.

    `checklists` is keyed by the normalized alert class. Issues follow class-key order, then item order.
    """
    wanted = {
        (key, item.tool)
        for key, checklist in checklists.items()
        for item in checklist.items
        if any(_checks_duration_value(rule) or _checks_number(rule) for rule in item.args.values())
    }
    calls_by_class_tool: dict[tuple[str, str], list[ToolCall]] = {}
    for case in cases:
        key = normalize_label(case.alert_class)
        for call in case.tool_calls:
            if (key, call.tool_name) in wanted:
                calls_by_class_tool.setdefault((key, call.tool_name), []).append(call)
    # Keyed by id(): span IDs repeat across traces, and every call stays alive in `cases`.
    arguments_by_call: dict[int, object] = {}
    issues: list[Issue] = []
    for key in sorted(checklists):
        checklist = checklists[key]
        for item in checklist.items:
            calls = calls_by_class_tool.get((key, item.tool), [])
            detail = _find_mismatch(item, calls, arguments_by_call)
            if detail:
                subject = f"{checklist.alert_class}/{item.id}"
                issues.append(Issue(IssueKind.RULE_TYPE_MISMATCH, subject, detail))
    return issues


@dataclass(frozen=True, slots=True)
class _Where:
    case_id: str
    item_id: str
    span_id: str
    issues: list[Issue]

    def report(self, kind: IssueKind, text: str) -> None:
        self.issues.append(
            Issue(kind, self.case_id, f"{self.item_id}: call {self.span_id}: {text}")
        )


def _evaluate_item(
    case_id: str,
    item: ChecklistItem,
    calls: list[ToolCall],
    arguments_by_span: dict[str, object],
    issues: list[Issue],
) -> ItemOutcome:
    if not calls:
        return ItemOutcome(ItemStatus.MISSED, MissedReason.NOT_CALLED)
    is_satisfied = False
    has_failed_call = False
    for call in calls:
        if call.is_failed:
            has_failed_call = True
        # Every successful call is checked, not just up to the first pass, so reports don't
        # depend on call order.
        elif _call_passes(case_id, item, call, arguments_by_span, issues):
            is_satisfied = True
    if is_satisfied:
        return ItemOutcome(ItemStatus.SATISFIED, None)
    if has_failed_call:
        return ItemOutcome(ItemStatus.FAILED, None)
    return ItemOutcome(ItemStatus.MISSED, MissedReason.WRONG_ARGUMENTS)


def _call_passes(
    case_id: str,
    item: ChecklistItem,
    call: ToolCall,
    arguments_by_span: dict[str, object],
    issues: list[Issue],
) -> bool:
    if not item.args:
        return True
    if call.span_id not in arguments_by_span:
        arguments, problem = _parse_arguments(call.arguments)
        if problem:
            issues.append(
                Issue(
                    IssueKind.UNREADABLE_ARGUMENTS,
                    case_id,
                    f"call {call.span_id}: arguments for tool '{call.tool_name}' {problem}",
                )
            )
        arguments_by_span[call.span_id] = arguments
    root = arguments_by_span[call.span_id]
    if root is _UNREADABLE:
        return False
    where = _Where(case_id, item.id, call.span_id, issues)
    # A list, not a generator: every rule runs so each unreadable value is reported.
    results = [
        _rule_passes(_resolve(root, _path(key)), rule, where) for key, rule in item.args.items()
    ]
    return all(results)


def _parse_arguments(arguments: str | dict[str, object] | None) -> tuple[object, str]:
    """Return (arguments, problem); problem is empty when the arguments could be read."""
    if arguments is None:
        return _MISSING, ""
    if not isinstance(arguments, str):
        return arguments, ""
    try:
        return json.loads(arguments), ""
    except RecursionError:
        return _UNREADABLE, "are nested too deeply to read"
    # ValueError also covers integers longer than Python's int-string conversion limit.
    except ValueError:
        return _UNREADABLE, "are not valid JSON"


def _resolve(root: object, path: tuple[str | int, ...]) -> object:
    value = root
    for part in path:
        if isinstance(value, dict) and isinstance(part, str):
            if part not in value:
                return _MISSING
            value = value[part]
        elif isinstance(value, list) and isinstance(part, int):
            if part >= len(value):
                return _MISSING
            value = value[part]
        else:
            return _MISSING
    return value


def _rule_passes(value: object, rule: ArgRule, where: _Where) -> bool:
    results: list[bool] = []
    # `equals: null` is a real rule, so whether it was given comes from the fields set.
    if "equals" in rule.model_fields_set:
        results.append(json_equal(value, rule.equals))
    if rule.in_ is not None:
        results.append(any(json_equal(value, option) for option in rule.in_))
    if rule.exists is not None:
        results.append((value is not _MISSING) is rule.exists)
    if rule.matches is not None:
        results.append(isinstance(value, str) and _pattern(rule.matches).search(value) is not None)
    if rule.min_duration is not None:
        results.append(_duration_passes(value, rule, _threshold(rule.min_duration), where))
    if rule.kql_min_ago is not None:
        results.append(_kql_passes(value, _threshold(rule.kql_min_ago), where))
    if rule.min is not None:
        results.append(_is_number(value) and value >= rule.min)
    if rule.max is not None:
        results.append(_is_number(value) and value <= rule.max)
    return all(results)


def _duration_passes(value: object, rule: ArgRule, threshold: timedelta, where: _Where) -> bool:
    if rule.start is not None and rule.end is not None:
        start = _resolve(value, _path(rule.start))
        end = _resolve(value, _path(rule.end))
        if not isinstance(start, str) or not isinstance(end, str):
            return False
        duration = timestamp_difference(start, end)
        if duration is None:
            where.report(
                IssueKind.UNREADABLE_DURATION,
                f"timestamps {_shorten(start)} and {_shorten(end)} could not be compared",
            )
            return False
        return duration >= threshold
    # Numbers fail without a report here; find_rule_type_mismatches warns once per item.
    if not isinstance(value, str):
        return False
    duration = parse_duration(value)
    if duration is None:
        where.report(IssueKind.UNREADABLE_DURATION, f"duration {_shorten(value)} could not be read")
        return False
    return duration >= threshold


def _kql_passes(value: object, threshold: timedelta, where: _Where) -> bool:
    if not isinstance(value, str):
        return False
    readable, unreadable = kql_lookbacks(value)
    for text in unreadable:
        where.report(
            IssueKind.UNREADABLE_KQL_TIMESPAN,
            f"lookback {_shorten(f'ago({text})')} could not be read",
        )
    return any(lookback >= threshold for lookback in readable)


def _find_mismatch(
    item: ChecklistItem, calls: list[ToolCall], arguments_by_call: dict[int, object]
) -> str:
    for key, rule in item.args.items():
        checks_duration = _checks_duration_value(rule)
        checks_number = _checks_number(rule)
        if not checks_duration and not checks_number:
            continue
        path = _path(key)
        for call in calls:
            if id(call) not in arguments_by_call:
                arguments_by_call[id(call)] = _parse_arguments(call.arguments)[0]
            value = _resolve(arguments_by_call[id(call)], path)
            if value is _MISSING:
                continue
            if checks_duration and _is_number(value):
                return (
                    f"'{key}': a call's value is a number, but min_duration reads text like "
                    "'24h'; for numbers use min or max"
                )
            if checks_number and not _is_number(value):
                return (
                    f"'{key}': a call's value is not a number, but min and max compare numbers; "
                    "for text like '24h' use min_duration"
                )
    return ""


def _checks_duration_value(rule: ArgRule) -> bool:
    # With start and end, min_duration reads two timestamps, not the value at the path.
    return rule.min_duration is not None and rule.start is None


def _checks_number(rule: ArgRule) -> bool:
    return rule.min is not None or rule.max is not None


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _shorten(text: str) -> str:
    if len(text) > _MAX_SHOWN:
        text = text[: _MAX_SHOWN - 3] + "..."
    return repr(text)


# Checklist strings are few and fixed, so each is parsed or compiled once for all cases.


@cache
def _path(text: str) -> tuple[str | int, ...]:
    return parse_path(text)


@cache
def _pattern(text: str) -> re.Pattern[str]:
    return re.compile(text)


@cache
def _threshold(text: str) -> timedelta:
    threshold = parse_duration(text)
    if threshold is None:
        raise ValueError(f"checklist duration '{text}' was not validated")
    return threshold
