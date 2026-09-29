"""OpenTelemetry GenAI semantic-convention names used by DetectTrace.

Pinned to one release so a convention change is a one-module edit (PRD §13).
Pinned: open-telemetry/semantic-conventions v1.41.1 (first release with every name below:
v1.38.0). v1.42.0 moved the GenAI names to open-telemetry/semantic-conventions-genai,
which has no release yet; the names are unchanged there, so re-pin when it ships one.
"""

SEMCONV_VERSION = "1.41.1"

OPERATION_ATTRIBUTE = "gen_ai.operation.name"
INVOKE_AGENT = "invoke_agent"
EXECUTE_TOOL = "execute_tool"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"
REQUEST_MODEL = "gen_ai.request.model"
ERROR_TYPE = "error.type"
