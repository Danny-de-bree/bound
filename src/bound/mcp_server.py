"""Local stdio MCP server for BOUND (v0.8.0).

Reads JSON-RPC 2.0 requests from stdin, dispatches to the shared BOUND
service layer, and writes JSON-RPC responses to stdout.

No external MCP library is required -- this is a pure stdio implementation
that follows the Model Context Protocol specification for tool discovery
and invocation.

Architecture rules:
- Never duplicate CLI or policy logic -- call the shared service layer.
- Never shell out to the CLI -- call Python directly.
- Keep the ``mcp`` library import optional (try/except).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from bound.services import (
    BoundaryEvaluateRequest,
    BoundaryService,
    CheckpointCreateRequest,
    CheckpointError,
    CheckpointListRequest,
    CheckpointService,
    EvaluateRequest,
    EvaluateWorkflowRequest,
    EvaluationInputError,
    EvaluationService,
    EvidenceCollectRequest,
    EvidenceService,
    PolicyExplainRequest,
    PolicyHashRequest,
    PolicyLoadError,
    PolicyService,
    PolicyValidateRequest,
    PolicyValidationError,
    RunFinishRequest,
    RunInspectRequest,
    RunListRequest,
    RunNotFoundError,
    RunService,
    RunStartRequest,
    ServiceError,
)

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"

# MCP protocol version advertised during the ``initialize`` handshake.
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO: dict[str, str] = {"name": "bound", "version": "0.8.0"}
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {}}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

ERROR_SERVICE: dict[type[ServiceError], int] = {
    PolicyLoadError: -32001,
    PolicyValidationError: -32002,
    RunNotFoundError: -32003,
    EvaluationInputError: -32004,
    CheckpointError: -32005,
}

_TYPE_MAP: dict[type, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}


def _json_schema_from_model(model: type) -> dict[str, Any]:
    """Build a JSON Schema object from a Pydantic model class.

    Args:
        model: A Pydantic ``BaseModel`` subclass.

    Returns:
        A JSON Schema ``dict`` suitable for MCP tool input_schema.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in model.model_fields.items():
        if field.annotation is None:
            continue
        if name == "store":
            continue
        is_required = field.is_required()
        if is_required:
            required.append(name)
        origin = getattr(field.annotation, "__origin__", None)
        if origin is not None:
            args = getattr(field.annotation, "__args__", ())
            non_none = [a for a in args if a is not type(None)]
            schema = _type_to_schema(non_none[0]) if non_none else {"type": "string"}
        else:
            schema = _type_to_schema(field.annotation)
        if field.description:
            schema["description"] = field.description
        properties[name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "title": model.__name__,
    }


def _type_to_schema(tp: type) -> dict[str, Any]:
    """Map a Python type to a minimal JSON Schema fragment.

    Args:
        tp: A Python type.

    Returns:
        A JSON Schema dict fragment.
    """
    if tp in _TYPE_MAP:
        return dict(_TYPE_MAP[tp])
    if tp is type(None):
        return {"type": "null"}
    return {"type": "string"}


class McpToolDef:
    """Definition of one MCP tool, binding a name to a service method."""

    __slots__ = ("name", "description", "request_model", "handler")

    def __init__(
        self,
        name: str,
        description: str,
        request_model: type,
        handler: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.request_model = request_model
        self.handler = handler

    def input_schema(self) -> dict[str, Any]:
        """Return the JSON Schema for this tool's parameters."""
        return _json_schema_from_model(self.request_model)

    def to_dict(self) -> dict[str, Any]:
        """Return the tool descriptor dict (for tools/list)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema(),
        }


_TOOLS: list[McpToolDef] = [
    McpToolDef(
        name="bound_policy_validate",
        description="Validate a BOUND policy YAML file against the schema.",
        request_model=PolicyValidateRequest,
        handler=lambda p: PolicyService.validate(PolicyValidateRequest(**p)),
    ),
    McpToolDef(
        name="bound_policy_explain",
        description="Explain a policy file's effective gates, weights, and budgets.",
        request_model=PolicyExplainRequest,
        handler=lambda p: PolicyService.explain(PolicyExplainRequest(**p)),
    ),
    McpToolDef(
        name="bound_policy_hash",
        description="Compute the canonical hash of a policy file.",
        request_model=PolicyHashRequest,
        handler=lambda p: PolicyService.hash(PolicyHashRequest(**p)),
    ),
    McpToolDef(
        name="bound_run_start",
        description="Start a new lineage run.",
        request_model=RunStartRequest,
        handler=lambda p: RunService.start(RunStartRequest(**p)),
    ),
    McpToolDef(
        name="bound_run_finish",
        description="Finish (close) a lineage run.",
        request_model=RunFinishRequest,
        handler=lambda p: RunService.finish(RunFinishRequest(**p)),
    ),
    McpToolDef(
        name="bound_run_list",
        description="List all lineage runs (newest first).",
        request_model=RunListRequest,
        handler=lambda p: RunService.list_runs(RunListRequest(**p)),
    ),
    McpToolDef(
        name="bound_run_inspect",
        description="Inspect a lineage run's full log.",
        request_model=RunInspectRequest,
        handler=lambda p: RunService.inspect(RunInspectRequest(**p)),
    ),
    McpToolDef(
        name="bound_evaluate",
        description="Evaluate an action with pre-supplied scores.",
        request_model=EvaluateRequest,
        handler=lambda p: EvaluationService.evaluate(EvaluateRequest(**p)),
    ),
    McpToolDef(
        name="bound_evaluate_workflow",
        description="Evaluate using coding-workflow signals.",
        request_model=EvaluateWorkflowRequest,
        handler=lambda p: EvaluationService.evaluate_workflow(EvaluateWorkflowRequest(**p)),
    ),
    McpToolDef(
        name="bound_evidence_collect",
        description="Record a collected-evidence event in lineage.",
        request_model=EvidenceCollectRequest,
        handler=lambda p: EvidenceService.collect(EvidenceCollectRequest(**p)),
    ),
    McpToolDef(
        name="bound_boundary_evaluate",
        description="Evaluate an executed step against its contract and policy config.",
        request_model=BoundaryEvaluateRequest,
        handler=lambda p: BoundaryService.evaluate(BoundaryEvaluateRequest(**p)),
    ),
    McpToolDef(
        name="bound_checkpoint_create",
        description="Create a BOUND-owned checkpoint for a run step.",
        request_model=CheckpointCreateRequest,
        handler=lambda p: CheckpointService.create(CheckpointCreateRequest(**p)),
    ),
    McpToolDef(
        name="bound_checkpoint_list",
        description="List all checkpoints for a run.",
        request_model=CheckpointListRequest,
        handler=lambda p: CheckpointService.list_checkpoints(CheckpointListRequest(**p)),
    ),
]

_TOOL_MAP: dict[str, McpToolDef] = {t.name: t for t in _TOOLS}


def _make_response(
    request_id: int | str | None,
    result: Any = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 response dict.

    Args:
        request_id: The request id from the client.
        result: The result payload (used when ``error`` is ``None``).
        error: Optional error object with ``code``, ``message``, ``data``.

    Returns:
        A JSON-RPC 2.0 response dict.
    """
    msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": request_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def _make_error(
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Build a JSON-RPC error object.

    Args:
        code: The error code (negative integer).
        message: A short human-readable message.
        data: Optional additional error data.

    Returns:
        A JSON-RPC error dict.
    """
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


def _service_error_code(exc: Exception) -> int:
    """Map a service exception to a JSON-RPC error code.

    Args:
        exc: The raised exception.

    Returns:
        A negative integer error code.
    """
    for exc_type, code in ERROR_SERVICE.items():
        if isinstance(exc, exc_type):
            return code
    return INTERNAL_ERROR


def _serialize_result(result: Any) -> Any:
    """Serialize a service response to a JSON-safe value.

    Pydantic models are converted via ``model_dump(mode="json")``.

    Args:
        result: The service method return value.

    Returns:
        A JSON-serializable value.
    """
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "_asdict"):
        return result._asdict()
    return result


def _handle_rpc_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request and return a response.

    Per JSON-RPC 2.0 (section 4), a request without an ``id`` is a
    *notification* and MUST NOT receive a response. In that case this
    function returns ``None`` and the caller MUST NOT write anything to
    stdout.

    Requests that carry an ``id`` are validated for the correct
    ``jsonrpc`` version ("2.0"); a missing or mismatched version yields an
    ``INVALID_REQUEST`` (-32600) error response.

    Args:
        request: The parsed JSON-RPC request dict.

    Returns:
        A JSON-RPC response dict, or ``None`` when ``request`` is a
        notification (no ``id`` present).
    """
    rid = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    # C2: notifications (no ``id`` key) receive no response.
    is_notification = "id" not in request

    # C1: handle the ``initialize`` lifecycle handshake.
    if method == "initialize":
        if is_notification:
            return None
        return _make_response(
            rid,
            result={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": SERVER_INFO,
                "capabilities": SERVER_CAPABILITIES,
            },
        )

    # C1: ``notifications/initialized`` is a notification (no response).
    if method == "notifications/initialized":
        return None

    # Notifications for any other method are processed silently (no response).
    if is_notification:
        return None

    # C3: validate the JSON-RPC version for requests that expect a response.
    if request.get("jsonrpc") != JSONRPC_VERSION:
        return _make_response(
            rid,
            error=_make_error(
                INVALID_REQUEST,
                f"Invalid Request: jsonrpc must be '{JSONRPC_VERSION}'",
            ),
        )

    if method == "tools/list":
        tools = [t.to_dict() for t in _TOOLS]
        return _make_response(rid, result={"tools": tools})

    if method == "tools/call":
        tool_name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}

        if not tool_name:
            return _make_response(
                rid,
                error=_make_error(INVALID_PARAMS, "Missing tool name in tools/call params"),
            )

        tool = _TOOL_MAP.get(tool_name)
        if tool is None:
            return _make_response(
                rid,
                error=_make_error(METHOD_NOT_FOUND, f"Unknown tool: {tool_name}"),
            )

        try:
            result = tool.handler(arguments)
            serialized = _serialize_result(result)
            content = [{"type": "text", "text": json.dumps(serialized, default=str)}]
            return _make_response(rid, result={"content": content})
        except ServiceError as exc:
            code = _service_error_code(exc)
            return _make_response(rid, error=_make_error(code, str(exc)))
        except ValidationError as exc:
            # W1/W2: map Pydantic validation failures to INVALID_PARAMS and
            # surface the failing fields in the error ``data`` field.
            return _make_response(
                rid,
                error=_make_error(
                    INVALID_PARAMS,
                    "Invalid tool parameters",
                    data=_validation_error_data(exc),
                ),
            )
        except Exception as exc:
            logger.exception("Unhandled error in tool %s", tool_name)
            return _make_response(rid, error=_make_error(INTERNAL_ERROR, str(exc)))

    return _make_response(
        rid,
        error=_make_error(METHOD_NOT_FOUND, f"Unknown method: {method}"),
    )


def _validation_error_data(exc: ValidationError) -> list[dict[str, Any]]:
    """Convert a Pydantic ``ValidationError`` into JSON-safe error details.

    Args:
        exc: The raised :class:`pydantic.ValidationError`.

    Returns:
        A list of per-field error dicts with ``loc``, ``msg``, and ``type``
        keys, safe for JSON serialisation.
    """
    details: list[dict[str, Any]] = []
    for err in exc.errors():
        details.append(
            {
                "loc": list(err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return details


def _handle_rpc_batch(requests: list[Any]) -> list[dict[str, Any]] | None:
    """Dispatch a JSON-RPC batch request.

    Per JSON-RPC 2.0, responses are not returned for batch elements that are
    notifications (no ``id``). If every element in the batch is a
    notification, ``None`` is returned so the caller writes nothing.

    Args:
        requests: A list of individual request objects.

    Returns:
        A list of JSON-RPC response dicts, or ``None`` when the batch
        contains only notifications.
    """
    responses: list[dict[str, Any]] = []
    for req in requests:
        if not isinstance(req, dict):
            responses.append(
                _make_response(
                    None,
                    error=_make_error(INVALID_REQUEST, "Batch element must be a JSON object"),
                )
            )
            continue
        resp = _handle_rpc_request(req)
        if resp is not None:
            responses.append(resp)
    return responses if responses else None


def run_mcp_server(*, once: bool = False, json_log: bool = False) -> int:
    """Run the stdio JSON-RPC MCP server loop.

    Reads one JSON-RPC object per line from stdin, dispatches, and writes
    one JSON-RPC object per line to stdout.

    Args:
        once: When ``True``, process a single request and exit.
        json_log: When ``True``, emit structured JSON log lines to stderr.

    Returns:
        Exit code (0 on success, 1 on fatal errors).
    """
    if json_log:
        _configure_json_logging()
    # W7: ensure log records never reach stdout (would corrupt JSON-RPC).
    _ensure_stderr_logging()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            if once:
                break
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _make_response(
                None,
                error=_make_error(PARSE_ERROR, f"Invalid JSON: {exc}"),
            )
            _write_response(response)
            if once:
                break
            continue

        try:
            if isinstance(request, list):
                response = _handle_rpc_batch(request)
            elif isinstance(request, dict):
                response = _handle_rpc_request(request)
            else:
                response = _make_response(
                    None,
                    error=_make_error(INVALID_REQUEST, "Request must be a JSON object or array"),
                )
        except Exception as exc:
            logger.exception("Fatal error processing request")
            response = _make_response(
                request.get("id") if isinstance(request, dict) else None,
                error=_make_error(INTERNAL_ERROR, str(exc)),
            )

        # C2: notifications and notification-only batches produce no output.
        if response is not None:
            _write_response(response)
        if once:
            break

    return 0


def _write_response(response: Any) -> None:
    """Write a JSON-RPC response to stdout.

    Args:
        response: The response object (dict or list).
    """
    sys.stdout.write(json.dumps(response, default=str) + "\n")
    sys.stdout.flush()


def _configure_json_logging() -> None:
    """Configure logging to emit JSON lines to stderr."""
    import logging

    class JsonFormatter(logging.Formatter):
        """Format log records as JSON lines."""

        def format(self, record: logging.LogRecord) -> str:
            return json.dumps(
                {
                    "ts": self.formatTime(record),
                    "level": record.levelname,
                    "name": record.name,
                    "msg": record.getMessage(),
                },
                default=str,
            )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


_stderr_logging_ready = False


def _ensure_stderr_logging() -> None:
    """Guarantee log output goes to stderr, never stdout (W7).

    The JSON-RPC stream lives on stdout, so any log record reaching stdout
    would corrupt the protocol. This removes any root handlers bound to
    stdout and installs a single stderr handler when none exists yet.
    """
    global _stderr_logging_ready
    if _stderr_logging_ready:
        return
    root = logging.getLogger()
    # Drop any handlers that write to stdout.
    for handler in list(root.handlers):
        stream = getattr(handler, "stream", None)
        if stream is sys.stdout:
            root.removeHandler(handler)
    # Ensure at least one stderr handler exists.
    has_stderr = any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    )
    if not has_stderr:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _stderr_logging_ready = True
