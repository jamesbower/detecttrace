"""Send four synthetic SOC triage cases to a local Langfuse over OTLP/HTTP.

Run with:
    uv run --with opentelemetry-sdk --with opentelemetry-exporter-otlp-proto-http \
        python send_spans.py

Only example.com addresses and documentation IP ranges appear in the data.
"""

# The OTLP HTTP exporter is supplied by `uv run --with`, not the project environment.
# pyright: reportMissingImports=false

import base64
import json

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode, set_span_in_context

ENDPOINT = "http://localhost:3000/api/public/otel/v1/traces"
PUBLIC_KEY = "pk-lf-local"
SECRET_KEY = "sk-lf-local"

# 2026-09-30T08:00:00Z in nanoseconds; cases start a minute apart.
BASE_NS = 1_790_755_200_000_000_000
SECOND = 1_000_000_000

auth = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()
exporter = OTLPSpanExporter(endpoint=ENDPOINT, headers={"Authorization": f"Basic {auth}"})
resource = Resource.create(
    {
        "service.name": "soc-agent",
        "service.version": "0.9.0",
        "soc.team": "tier1-lab",
    }
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(SimpleSpanProcessor(exporter))
tracer = provider.get_tracer("detecttrace.fixture", "1.0.0")

LONG_NOTE = (
    "Analyst note: sign-in from 203.0.113.45 followed by 198.51.100.23 within "
    "eleven minutes; both addresses belong to documentation ranges and appear in "
    "this synthetic fixture only. " * 3
).strip()


def tool_span(parent_ctx, start_ns, name, arguments, result, *, extra=None, error=None):
    attributes = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": f"call_{name}_{start_ns // SECOND % 100000}",
        "gen_ai.tool.call.arguments": json.dumps(arguments),
    }
    if result is not None:
        attributes["gen_ai.tool.call.result"] = json.dumps(result)
    attributes.update(extra or {})
    span = tracer.start_span(
        f"execute_tool {name}",
        context=parent_ctx,
        start_time=start_ns,
        attributes=attributes,
    )
    if error:
        span.set_attribute("error.type", error[0])
        span.set_status(Status(StatusCode.ERROR, error[1]))
    span.end(end_time=start_ns + 2 * SECOND)


def chat_span(parent_ctx, start_ns):
    span = tracer.start_span(
        "chat example-model",
        context=parent_ctx,
        start_time=start_ns,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "example",
            "gen_ai.request.model": "example-model",
            "gen_ai.usage.input_tokens": 1200,
            "gen_ai.usage.output_tokens": 180,
        },
    )
    span.end(end_time=start_ns + SECOND)


def case(index, case_id, alert_class, verdict, prompt_version, *, via_langfuse_metadata=False):
    start = BASE_NS + index * 60 * SECOND
    attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "soc-triage",
        "detecttrace.case_id": case_id,
        "detecttrace.alert_class": alert_class,
        "detecttrace.verdict": verdict,
    }
    if prompt_version is not None:
        attributes["detecttrace.prompt_version"] = prompt_version
    if via_langfuse_metadata:
        attributes["langfuse.trace.metadata.case_id"] = case_id
        attributes["langfuse.trace.metadata.alert_class"] = alert_class
        attributes["langfuse.trace.metadata.verdict"] = verdict
    root = tracer.start_span("invoke_agent soc-triage", start_time=start, attributes=attributes)
    ctx = set_span_in_context(root)
    user = f"user{index}@example.com"
    chat_span(ctx, start + 1 * SECOND)
    return root, ctx, start, user


def main():
    # Case 1: impossible travel, true positive, v1, with typed and array attributes,
    # one argument value longer than 300 characters, and the same long text as a
    # plain span attribute (Langfuse moves tool arguments to input, not metadata).
    root, ctx, start, user = case(1, "CASE-9001", "impossible_travel", "TruePositive", "v1")
    tool_span(
        ctx,
        start + 3 * SECOND,
        "get_signin_logs",
        {"user": user, "range": "24h", "note": LONG_NOTE},
        {"signins": [{"ip": "203.0.113.45"}, {"ip": "198.51.100.23"}]},
        extra={
            "detecttrace.result_count": 2,
            "detecttrace.risk_score": 0.87,
            "detecttrace.mfa_satisfied": False,
            "detecttrace.source_ips": ["203.0.113.45", "198.51.100.23"],
            "detecttrace.analyst_note": LONG_NOTE,
        },
    )
    tool_span(
        ctx, start + 6 * SECOND, "get_user_profile", {"user": user}, {"department": "Finance"}
    )
    root.end(end_time=start + 10 * SECOND)

    # Case 2: OAuth consent, benign positive, v2, with one failed tool call.
    root, ctx, start, user = case(2, "CASE-9002", "oauth_consent", "BenignPositive", "v2")
    tool_span(
        ctx,
        start + 3 * SECOND,
        "get_oauth_grants",
        {"user": user, "range": "7d"},
        None,
        error=("TimeoutError", "grant service did not answer within 30s"),
    )
    tool_span(ctx, start + 6 * SECOND, "get_user_profile", {"user": user}, {"department": "Sales"})
    root.end(end_time=start + 10 * SECOND)

    # Case 3: impossible travel, false positive, no prompt version, with a nested
    # sub-agent (invoke_agent without case attributes) that makes its own tool call.
    root, ctx, start, user = case(3, "CASE-9003", "impossible_travel", "FalsePositive", None)
    tool_span(
        ctx, start + 3 * SECOND, "get_signin_logs", {"user": user, "range": "24h"}, {"signins": []}
    )
    sub = tracer.start_span(
        "invoke_agent ip-enrichment",
        context=ctx,
        start_time=start + 5 * SECOND,
        attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "ip-enrichment"},
    )
    sub_ctx = set_span_in_context(sub)
    tool_span(
        sub_ctx,
        start + 6 * SECOND,
        "lookup_ip_reputation",
        {"ip": "192.0.2.10"},
        {"asn": 64500, "reputation": "clean"},
    )
    sub.end(end_time=start + 8 * SECOND)
    root.end(end_time=start + 10 * SECOND)

    # Case 4: OAuth consent, "Closed - Benign", v1, case attributes also sent as
    # langfuse.trace.metadata.* to see where Langfuse puts them.
    root, ctx, start, user = case(
        4, "CASE-9004", "oauth_consent", "Closed - Benign", "v1", via_langfuse_metadata=True
    )
    tool_span(
        ctx, start + 3 * SECOND, "get_oauth_grants", {"user": user, "range": "7d"}, {"grants": []}
    )
    root.end(end_time=start + 10 * SECOND)

    provider.force_flush()
    provider.shutdown()


if __name__ == "__main__":
    main()
