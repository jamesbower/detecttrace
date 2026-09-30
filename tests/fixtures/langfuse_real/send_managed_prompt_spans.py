"""Send four synthetic SOC triage cases whose generations link a Langfuse managed prompt.

The managed prompt "soc-triage" (versions 1 and 2) must exist first; SOURCE.txt lists the
requests that created it. Run with:
    uv run --no-project --with opentelemetry-sdk \
        --with opentelemetry-exporter-otlp-proto-http python send_managed_prompt_spans.py

No agent span carries detecttrace.prompt_version: the version is only on the generation,
through the attributes Langfuse reads to link a generation to a managed prompt.
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
from opentelemetry.trace import set_span_in_context

ENDPOINT = "http://localhost:3000/api/public/otel/v1/traces"
PUBLIC_KEY = "pk-lf-local"
SECRET_KEY = "sk-lf-local"
PROMPT_NAME = "soc-triage"

# 2026-09-30T09:00:00Z in nanoseconds; cases start a minute apart.
BASE_NS = 1_790_758_800_000_000_000
SECOND = 1_000_000_000

auth = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()
exporter = OTLPSpanExporter(endpoint=ENDPOINT, headers={"Authorization": f"Basic {auth}"})
resource = Resource.create(
    {"service.name": "soc-agent", "service.version": "0.9.1", "soc.team": "tier1-lab"}
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(SimpleSpanProcessor(exporter))
tracer = provider.get_tracer("detecttrace.fixture", "1.0.0")


def tool_span(parent_ctx, start_ns, name, arguments, result):
    span = tracer.start_span(
        f"execute_tool {name}",
        context=parent_ctx,
        start_time=start_ns,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.type": "function",
            "gen_ai.tool.call.id": f"call_{name}_{start_ns // SECOND % 100000}",
            "gen_ai.tool.call.arguments": json.dumps(arguments),
            "gen_ai.tool.call.result": json.dumps(result),
        },
    )
    span.end(end_time=start_ns + 2 * SECOND)


def chat_span(parent_ctx, start_ns, prompt_version):
    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "example",
        "gen_ai.request.model": "example-model",
        "gen_ai.usage.input_tokens": 1200,
        "gen_ai.usage.output_tokens": 180,
    }
    if prompt_version is not None:
        attributes["langfuse.observation.prompt.name"] = PROMPT_NAME
        attributes["langfuse.observation.prompt.version"] = prompt_version
    span = tracer.start_span(
        "chat example-model", context=parent_ctx, start_time=start_ns, attributes=attributes
    )
    span.end(end_time=start_ns + SECOND)


def case(index, case_id, alert_class, verdict, *, root_extra=None):
    start = BASE_NS + index * 60 * SECOND
    attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "soc-triage",
        "detecttrace.case_id": case_id,
        "detecttrace.alert_class": alert_class,
        "detecttrace.verdict": verdict,
    }
    attributes.update(root_extra or {})
    root = tracer.start_span("invoke_agent soc-triage", start_time=start, attributes=attributes)
    return root, set_span_in_context(root), start, f"user{index}@example.com"


def main():
    # Case 1: prompt version 1, sent as an integer.
    root, ctx, start, user = case(1, "CASE-9101", "impossible_travel", "TruePositive")
    chat_span(ctx, start + 1 * SECOND, 1)
    tool_span(
        ctx,
        start + 3 * SECOND,
        "get_signin_logs",
        {"user": user, "range": "24h"},
        {"signins": [{"ip": "203.0.113.45"}, {"ip": "198.51.100.23"}]},
    )
    tool_span(
        ctx, start + 6 * SECOND, "get_user_profile", {"user": user}, {"department": "Finance"}
    )
    root.end(end_time=start + 10 * SECOND)

    # Case 2: prompt version 2, sent as an integer.
    root, ctx, start, user = case(2, "CASE-9102", "oauth_consent", "BenignPositive")
    chat_span(ctx, start + 1 * SECOND, 2)
    tool_span(ctx, start + 3 * SECOND, "get_oauth_grants", {"user": user}, {"grants": []})
    root.end(end_time=start + 10 * SECOND)

    # Case 3: prompt version 2 on two generations, the second sent as the string "2".
    root, ctx, start, user = case(3, "CASE-9103", "impossible_travel", "FalsePositive")
    chat_span(ctx, start + 1 * SECOND, 2)
    tool_span(
        ctx, start + 3 * SECOND, "get_signin_logs", {"user": user, "range": "24h"}, {"signins": []}
    )
    chat_span(ctx, start + 6 * SECOND, "2")
    root.end(end_time=start + 10 * SECOND)

    # Case 4: no prompt linked to the generation. The agent span carries the prompt
    # attributes instead, to see whether Langfuse links a prompt to a non-generation.
    root, ctx, start, user = case(
        4,
        "CASE-9104",
        "oauth_consent",
        "TruePositive",
        root_extra={
            "langfuse.observation.prompt.name": PROMPT_NAME,
            "langfuse.observation.prompt.version": 2,
        },
    )
    chat_span(ctx, start + 1 * SECOND, None)
    tool_span(ctx, start + 3 * SECOND, "get_oauth_grants", {"user": user}, {"grants": []})
    root.end(end_time=start + 10 * SECOND)

    provider.force_flush()
    provider.shutdown()


if __name__ == "__main__":
    main()
