"""Decide, for each case, which checklist items its tool calls satisfied."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum, StrEnum
from functools import cache
from typing import NoReturn, TypeGuard

from detecttrace.checklist import ArgRule, Checklist, ChecklistItem, parse_path
from detecttrace.config import normalize_label
from detecttrace.durations import kql_lookbacks, parse_duration, timestamp_difference
from detecttrace.model import Case, Issue, IssueKind, ToolCall

# Argument values can be sensitive, so details show at most this much of one.
_MAX_SHOWN = 80
# A path with no value. Distinct from None, because JSON null is a value.
_MISSING = object()
# Arguments that could not be parsed. Distinct from _MISSING, because a call with no arguments
# can still pass `exists: false`, while an unreadable call passes nothing.
_UNREADABLE = object()
# A call read by many items, or a query with thousands of bad lookbacks, should cost a few
# issues, not thousands.
_MAX_REPORTS_PER_CALL = 5
_UNREADABLE_RULE = "arguments could not be read"
_NUMERIC_TEXT = re.compile(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")


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
    # Set only for WRONG_ARGUMENTS: "<path>: <rule>" for the first rule the first call failed,
    # or "arguments could not be read". Raw text from the checklist, not escaped.
    failed_rule: str | None = None


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
    """Give each checklist item a status for this case; unreadable input is appended to `issues`.

    Unreadable arguments are reported only for calls that an item with argument rules looks at;
    calls to other tools are never parsed.
    """
    calls_by_tool: dict[str, list[ToolCall]] = {}
    for call in case.tool_calls:
        calls_by_tool.setdefault(call.tool_name, []).append(call)
    state = _CaseState(case.case_id, issues)
    outcomes = tuple(
        _evaluate_item(item, calls_by_tool.get(item.tool, []), state) for item in checklist.items
    )
    return CaseEvidence(case.case_id, outcomes)


def find_rule_type_mismatches(
    cases: Sequence[Case], checklists: Mapping[str, Checklist]
) -> list[Issue]:
    """Warn once per checklist item with a rule that meets a value of a type it can never pass on,
    naming the first case where that happened.

    Covered: min and max on a non-number; min_duration, kql_min_ago and matches on a non-string;
    a non-string start or end timestamp; equals or in expecting only numbers on text that looks
    like a number, or expecting only text on a number.

    `checklists` is keyed by the normalized alert class. Issues follow class-key order, then item order.
    """
    # Per (class key, tool): the items that have no warning yet, by index, with their type checks.
    waiting: dict[tuple[str, str], list[tuple[int, list[_TypeCheck]]]] = {}
    for key, checklist in checklists.items():
        for index, item in enumerate(checklist.items):
            checks = _type_checks(item)
            if checks:
                waiting.setdefault((key, item.tool), []).append((index, checks))
    details: dict[tuple[str, int], str] = {}
    for case in cases:
        if not waiting:
            break
        key = normalize_label(case.alert_class)
        for call in case.tool_calls:
            entries = waiting.get((key, call.tool_name))
            if entries is None:
                continue
            # Parsed once per call and dropped after it, so memory stays flat at any case count.
            root = _parse_arguments(call.arguments)[0]
            if root is _UNREADABLE:
                continue
            remaining: list[tuple[int, list[_TypeCheck]]] = []
            for index, checks in entries:
                detail = _find_mismatch(root, checks)
                if detail:
                    details[(key, index)] = f"{detail}; first seen in case {case.case_id}"
                else:
                    remaining.append((index, checks))
            if not remaining:
                del waiting[(key, call.tool_name)]
            elif len(remaining) < len(entries):
                waiting[(key, call.tool_name)] = remaining
    issues: list[Issue] = []
    for key in sorted(checklists):
        checklist = checklists[key]
        for index, item in enumerate(checklist.items):
            if (key, index) in details:
                subject = f"{checklist.alert_class}/{item.id}"
                issues.append(Issue(IssueKind.RULE_TYPE_MISMATCH, subject, details[(key, index)]))
    return issues


def find_missing_tool_arguments(
    cases: Sequence[Case], checklists: Mapping[str, Checklist]
) -> list[Issue]:
    """Warn once per checklist item with argument rules whose tool is called in cases of its
    class, but never with arguments.

    That pattern points at a wrong arguments mapping rather than at the agent, which would
    otherwise show only as every such item missed. One issue per item, not per call, so tools
    that really take no arguments stay quiet.

    `checklists` is keyed by the normalized alert class. Issues follow class-key order, then item order.
    """
    watched = {
        (key, item.tool)
        for key, checklist in checklists.items()
        for item in checklist.items
        if item.args
    }
    call_counts: dict[tuple[str, str], int] = {}
    with_arguments: set[tuple[str, str]] = set()
    for case in cases:
        key = normalize_label(case.alert_class)
        for call in case.tool_calls:
            pair = (key, call.tool_name)
            if pair in watched:
                call_counts[pair] = call_counts.get(pair, 0) + 1
                if call.arguments is not None:
                    with_arguments.add(pair)
    issues: list[Issue] = []
    for key in sorted(checklists):
        checklist = checklists[key]
        for item in checklist.items:
            count = call_counts.get((key, item.tool), 0)
            if item.args and count and (key, item.tool) not in with_arguments:
                noun = "call" if count == 1 else "calls"
                issues.append(
                    Issue(
                        IssueKind.MISSING_TOOL_ARGUMENTS,
                        f"{checklist.alert_class}/{item.id}",
                        f"{count:,} {noun} to '{item.tool}', none with arguments",
                    )
                )
    return issues


@dataclass(slots=True)
class _CaseState:
    """What the items of one case share, so a call read by several items is handled once."""

    case_id: str
    issues: list[Issue]
    arguments_by_span: dict[str, object] = field(default_factory=dict)
    reported: set[tuple[str, IssueKind, str]] = field(default_factory=set)
    report_counts: dict[tuple[str, IssueKind], int] = field(default_factory=dict)

    def report(self, kind: IssueKind, item_id: str, span_id: str, text: str) -> bool:
        """Append an issue unless the call already has it; False once the call's cap is full."""
        if (span_id, kind, text) in self.reported:
            return True
        count = self.report_counts.get((span_id, kind), 0)
        if count >= _MAX_REPORTS_PER_CALL:
            return False
        self.reported.add((span_id, kind, text))
        self.report_counts[(span_id, kind)] = count + 1
        self.issues.append(Issue(kind, self.case_id, f"{item_id}: call {span_id}: {text}"))
        return count + 1 < _MAX_REPORTS_PER_CALL


@dataclass(frozen=True, slots=True)
class _Where:
    state: _CaseState
    item_id: str
    span_id: str

    def report(self, kind: IssueKind, text: str) -> bool:
        return self.state.report(kind, self.item_id, self.span_id, text)


def _evaluate_item(item: ChecklistItem, calls: list[ToolCall], state: _CaseState) -> ItemOutcome:
    if not calls:
        return ItemOutcome(ItemStatus.MISSED, MissedReason.NOT_CALLED)
    is_satisfied = False
    has_failed_call = False
    first_failed_rule: str | None = None
    for call in calls:
        if call.is_failed:
            has_failed_call = True
            continue
        # Every successful call is checked, not just up to the first pass, so reports don't
        # depend on call order.
        failed_rule = _find_failed_rule(item, call, state)
        if failed_rule is None:
            is_satisfied = True
        elif first_failed_rule is None:
            first_failed_rule = failed_rule
    if is_satisfied:
        return ItemOutcome(ItemStatus.SATISFIED, None)
    if has_failed_call:
        return ItemOutcome(ItemStatus.FAILED, None)
    return ItemOutcome(ItemStatus.MISSED, MissedReason.WRONG_ARGUMENTS, first_failed_rule)


def _find_failed_rule(item: ChecklistItem, call: ToolCall, state: _CaseState) -> str | None:
    """Describe the first rule the call fails (paths in checklist order); None if all pass."""
    if not item.args:
        return None
    if call.span_id not in state.arguments_by_span:
        arguments, problem = _parse_arguments(call.arguments)
        if problem:
            state.issues.append(
                Issue(
                    IssueKind.UNREADABLE_ARGUMENTS,
                    state.case_id,
                    f"call {call.span_id}: arguments for tool '{call.tool_name}' {problem}",
                )
            )
        state.arguments_by_span[call.span_id] = arguments
    root = state.arguments_by_span[call.span_id]
    if root is _UNREADABLE:
        return _UNREADABLE_RULE
    where = _Where(state, item.id, call.span_id)
    # A list, not a generator: every rule runs so each unreadable value is reported.
    failures = [
        _find_failed_check(_resolve(root, _path(key)), rule, where)
        for key, rule in item.args.items()
    ]
    for key, failure in zip(item.args, failures, strict=True):
        if failure is not None:
            return f"{key}: {failure}"
    return None


def _parse_arguments(arguments: str | dict[str, object] | None) -> tuple[object, str]:
    """Return (arguments, problem); problem is empty when the arguments could be read."""
    if arguments is None:
        return _MISSING, ""
    if not isinstance(arguments, str):
        # A map from the trace can hold a NaN or infinite double, which a JSON string cannot.
        try:
            has_non_finite = _has_non_finite(arguments)
        except RecursionError:
            return _UNREADABLE, "are nested too deeply to read"
        if has_non_finite:
            return _UNREADABLE, "contain NaN or Infinity, which is not valid JSON"
        return arguments, ""
    try:
        return _DECODER.decode(arguments), ""
    except RecursionError:
        return _UNREADABLE, "are nested too deeply to read"
    except _NonFiniteNumberError:
        return _UNREADABLE, "contain NaN or Infinity, which is not valid JSON"
    # ValueError also covers integers longer than Python's int-string conversion limit.
    except ValueError:
        return _UNREADABLE, "are not valid JSON"


def _has_non_finite(value: object) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_has_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_non_finite(item) for item in value)
    return False


class _NonFiniteNumberError(Exception):
    pass


def _reject_non_finite(constant: str) -> NoReturn:
    # Python's json accepts NaN and Infinity, but JSON doesn't, and NaN would never compare equal.
    raise _NonFiniteNumberError(constant)


# One shared decoder: json.loads with any keyword builds a new decoder on every call.
_DECODER = json.JSONDecoder(parse_constant=_reject_non_finite)


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


def _find_failed_check(value: object, rule: ArgRule, where: _Where) -> str | None:
    """Name the first check under one path that fails, in a fixed order; None when all pass."""
    results: list[tuple[str, bool]] = []
    # `equals: null` is a real rule, so whether it was given comes from the fields set.
    if "equals" in rule.model_fields_set:
        results.append(("equals", json_equal(value, rule.equals)))
    if rule.in_ is not None:
        results.append(("in", any(json_equal(value, option) for option in rule.in_)))
    if rule.exists is not None:
        results.append(("exists", (value is not _MISSING) is rule.exists))
    if rule.matches is not None:
        is_match = isinstance(value, str) and _pattern(rule.matches).search(value) is not None
        results.append(("matches", is_match))
    if rule.min_duration is not None:
        is_long = _duration_passes(value, rule, _threshold(rule.min_duration), where)
        results.append(("min_duration", is_long))
    if rule.kql_min_ago is not None:
        results.append(("kql_min_ago", _kql_passes(value, _threshold(rule.kql_min_ago), where)))
    if rule.min is not None:
        results.append(("min", _is_number(value) and value >= rule.min))
    if rule.max is not None:
        results.append(("max", _is_number(value) and value <= rule.max))
    return next((name for name, is_passed in results if not is_passed), None)


def _duration_passes(value: object, rule: ArgRule, threshold: timedelta, where: _Where) -> bool:
    if rule.start is not None and rule.end is not None:
        start = _resolve(value, _path(rule.start))
        end = _resolve(value, _path(rule.end))
        # Non-strings fail without a report here; find_rule_type_mismatches warns once per item.
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
    for text in sorted(set(unreadable)):
        if not where.report(
            IssueKind.UNREADABLE_KQL_TIMESPAN,
            f"lookback {_shorten(f'ago({text})')} could not be read",
        ):
            break
    return any(lookback >= threshold for lookback in readable)


class _Reads(Enum):
    NUMBER = "number"  # min and max
    TEXT = "text"  # min_duration, kql_min_ago and matches
    TIMESTAMP = "timestamp"  # start and end
    LIKE_NUMBERS = "like_numbers"  # equals or in, expecting only numbers
    LIKE_TEXT = "like_text"  # equals or in, expecting only text


@dataclass(frozen=True, slots=True)
class _TypeCheck:
    shown: str
    path: tuple[str | int, ...]
    rule_name: str
    reads: _Reads


def _type_checks(item: ChecklistItem) -> list[_TypeCheck]:
    checks: list[_TypeCheck] = []
    for key, rule in item.args.items():
        path = _path(key)
        if rule.min is not None or rule.max is not None:
            names = [name for name in ("min", "max") if getattr(rule, name) is not None]
            checks.append(_TypeCheck(key, path, " and ".join(names), _Reads.NUMBER))
        if rule.min_duration is not None:
            if rule.start is not None and rule.end is not None:
                # With start and end, min_duration reads two timestamps, not the value at the path.
                for name, sub in (("start", rule.start), ("end", rule.end)):
                    shown = sub if key == "$" else f"{key}.{sub}"
                    checks.append(_TypeCheck(shown, path + _path(sub), name, _Reads.TIMESTAMP))
            else:
                checks.append(_TypeCheck(key, path, "min_duration", _Reads.TEXT))
        if rule.kql_min_ago is not None:
            checks.append(_TypeCheck(key, path, "kql_min_ago", _Reads.TEXT))
        if rule.matches is not None:
            checks.append(_TypeCheck(key, path, "matches", _Reads.TEXT))
        if "equals" in rule.model_fields_set:
            checks.extend(_expected_value_checks(key, path, "equals", [rule.equals]))
        if rule.in_:
            checks.extend(_expected_value_checks(key, path, "in", rule.in_))
    return checks


def _expected_value_checks(
    key: str, path: tuple[str | int, ...], rule_name: str, expected: Sequence[object]
) -> list[_TypeCheck]:
    if all(_is_number(option) for option in expected):
        return [_TypeCheck(key, path, rule_name, _Reads.LIKE_NUMBERS)]
    if all(isinstance(option, str) for option in expected):
        return [_TypeCheck(key, path, rule_name, _Reads.LIKE_TEXT)]
    return []


def _find_mismatch(root: object, checks: list[_TypeCheck]) -> str:
    for check in checks:
        value = _resolve(root, check.path)
        if value is _MISSING:
            continue
        advice = _advice(check, value)
        if advice:
            return f"'{check.shown}' ({check.rule_name}): the value is {_describe(value)}; {advice}"
    return ""


def _advice(check: _TypeCheck, value: object) -> str:
    """Say how to fix a rule that can never pass on `value`, or "" when it can."""
    noun = "value" if check.rule_name == "equals" else "values"
    if check.reads is _Reads.LIKE_NUMBERS:
        is_numeric_text = isinstance(value, str) and _NUMERIC_TEXT.fullmatch(value) is not None
        return f"quote the expected {noun} in the checklist" if is_numeric_text else ""
    if check.reads is _Reads.LIKE_TEXT:
        return f"unquote the expected {noun} in the checklist" if _is_number(value) else ""
    if check.reads is _Reads.NUMBER:
        if _is_number(value):
            return ""
        return "use min_duration or equals" if isinstance(value, str) else "this rule can't read it"
    if isinstance(value, str):
        return ""
    if check.reads is _Reads.TIMESTAMP:
        return "this rule reads ISO 8601 timestamps"
    if check.rule_name == "matches":
        return "this rule reads text"
    if check.rule_name == "min_duration" and _is_number(value):
        return "use min"
    return "this rule can't read it"


def _describe(value: object) -> str:
    if isinstance(value, str):
        return "text"
    if isinstance(value, bool):
        return "a boolean"
    if _is_number(value):
        return "a number"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "a list"
    return "an object"


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
    # Checklist validation already rejected unreadable durations, so only a programming error
    # (a rule built without validation) gets here.
    if threshold is None:
        raise ValueError(f"checklist duration '{text}' was not validated")
    return threshold
