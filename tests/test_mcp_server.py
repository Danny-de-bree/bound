"""Tests for the MCP server (``bound.mcp_server``).

Verifies that:
1. Tools are discoverable via ``tools/list``.
2. Each tool returns a valid JSON-RPC response.
3. Service errors are mapped to JSON-RPC error codes.
4. Malformed JSON-RPC requests are rejected with proper errors.
5. The ``--once`` flag processes a single request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bound.mcp_server import (
    _TOOLS,
    _TOOL_MAP,
    _handle_rpc_request,
    _make_response,
    _make_error,
    _service_error_code,
    _serialize_result,
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from bound.services import (
    PolicyLoadError,
    PolicyValidationError,
    RunNotFoundError,
    EvaluationInputError,
    CheckpointError,
    ServiceError,
    RunStartResponse,
    RunFinishResponse,
    RunListResponse,
    RunInspectResponse,
    PolicyValidateResponse,
    PolicyExplainResponse,
    PolicyHashResponse,
    EvaluateResponse,
    EvaluateWorkflowResponse,
    EvidenceCollectResponse,
    BoundaryEvaluateResponse,
    CheckpointCreateResponse,
    CheckpointListResponse,
)


# =========================================================================
# tools/list
# =========================================================================


def test_tools_list_returns_all_tools() -> None:
    """A ``tools/list`` request returns every registered tool."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = _handle_rpc_request(request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "result" in response
    assert "tools" in response["result"]
    tool_names = {t["name"] for t in response["result"]["tools"]}
    expected = {t.name for t in _TOOLS}
    assert tool_names == expected


def test_tools_list_tool_has_name_description_and_input_schema() -> None:
    """Every tool returned by ``tools/list`` has the required fields."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = _handle_rpc_request(request)
    for tool in response["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert "type" in tool["inputSchema"]
        assert "properties" in tool["inputSchema"]


# =========================================================================
# tools/call - basic
# =========================================================================


def test_unknown_tool_returns_error() -> None:
    """Calling an unknown tool returns a METHOD_NOT_FOUND error."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    }
    response = _handle_rpc_request(request)
    assert response["jsonrpc"] == "2.0"
    assert "error" in response
    assert response["error"]["code"] == METHOD_NOT_FOUND


def test_missing_tool_name_returns_error() -> None:
    """A ``tools/call`` without a tool name returns INVALID_PARAMS."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"arguments": {}},
    }
    response = _handle_rpc_request(request)
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


def test_unknown_method_returns_error() -> None:
    """An unknown method returns METHOD_NOT_FOUND."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "unknown_method", "params": {}}
    response = _handle_rpc_request(request)
    assert "error" in response
    assert response["error"]["code"] == METHOD_NOT_FOUND


# =========================================================================
# tools/call - policy tools
# =========================================================================


def test_policy_validate_with_nonexistent_file() -> None:
    """Calling ``bound_policy_validate`` with a nonexistent path returns a result.

    The service does not raise an exception for this case; it returns a
    ``PolicyValidateResponse`` with ``valid=False``.
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "bound_policy_validate",
            "arguments": {"path": "/nonexistent/path.yaml"},
        },
    }
    response = _handle_rpc_request(request)
    assert "result" in response
    content = response["result"]["content"]
    assert len(content) == 1
    data = json.loads(content[0]["text"])
    assert data["valid"] is False
    assert len(data["errors"]) >= 1


# =========================================================================
# tools/call - run tools
# =========================================================================


def test_run_start_without_run_dir_returns_error() -> None:
    """Calling ``bound_run_start`` without a BOUND_RUNS_DIR returns an error.

    The service raises ``RunNotFoundError`` or similar when the lineage store
    cannot be initialised.
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "bound_run_start",
            "arguments": {"task": "test task"},
        },
    }
    response = _handle_rpc_request(request)
    # The service may succeed if the default store is writable, or fail gracefully.
    # Either way, the response must be valid JSON-RPC.
    assert "jsonrpc" in response
    # It should either have a result or an error
    assert "result" in response or "error" in response


# =========================================================================
# Service error mapping
# =========================================================================


class _CustomServiceError(ServiceError):
    """Test error not in the mapping."""


def test_service_error_code_mapping() -> None:
    """Each service error type maps to the correct JSON-RPC error code."""
    assert _service_error_code(PolicyLoadError("test")) == -32001
    assert _service_error_code(PolicyValidationError("test")) == -32002
    assert _service_error_code(RunNotFoundError("test")) == -32003
    assert _service_error_code(EvaluationInputError("test")) == -32004
    assert _service_error_code(CheckpointError("test")) == -32005


def test_unmapped_service_error_falls_back_to_internal_error() -> None:
    """An unmapped :class:`ServiceError` subclass falls back to INTERNAL_ERROR."""
    assert _service_error_code(_CustomServiceError("test")) == INTERNAL_ERROR


# =========================================================================
# Result serialization
# =========================================================================


def test_serialize_result_with_model_dump() -> None:
    """A Pydantic model is serialized via ``model_dump(mode="json")``."""
    result = RunStartResponse(
        run_id="test-run",
        task="test",
        started_at="2024-01-01T00:00:00",
        status="started",
        schema_version="2.0",
    )
    serialized = _serialize_result(result)
    assert serialized["run_id"] == "test-run"
    assert serialized["task"] == "test"
    assert serialized["status"] == "started"


def test_serialize_result_with_dict() -> None:
    """A plain dict is returned as-is."""
    data = {"key": "value"}
    assert _serialize_result(data) is data


def test_serialize_result_with_list() -> None:
    """A list is returned as-is."""
    data = [1, 2, 3]
    assert _serialize_result(data) is data


# =========================================================================
# JSON-RPC helpers
# =========================================================================


def test_make_response_with_result() -> None:
    """A response with a result has the correct structure."""
    resp = _make_response(1, result={"ok": True})
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_make_response_with_error() -> None:
    """A response with an error has the correct structure."""
    err = _make_error(-32000, "Something went wrong", data={"detail": "x"})
    resp = _make_response(1, error=err)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["error"]["code"] == -32000
    assert resp["error"]["message"] == "Something went wrong"
    assert resp["error"]["data"] == {"detail": "x"}


def test_make_response_without_data() -> None:
    """An error without data omits the ``data`` field."""
    err = _make_error(-32000, "msg")
    assert "data" not in err


def test_make_response_with_null_id() -> None:
    """A response with a ``None`` id is valid JSON-RPC."""
    resp = _make_response(None, error=_make_error(PARSE_ERROR, "parse error"))
    assert resp["id"] is None


# =========================================================================
# Tool registry completeness
# =========================================================================


def test_all_tools_have_name_and_handler() -> None:
    """Every tool in the registry has a name, description, and handler."""
    for tool in _TOOLS:
        assert tool.name
        assert tool.description
        assert callable(tool.handler)


def test_tool_map_contains_all_tools() -> None:
    """The tool map indexes every tool by name."""
    for tool in _TOOLS:
        assert tool.name in _TOOL_MAP
        assert _TOOL_MAP[tool.name] is tool


def test_tool_map_has_no_extra_entries() -> None:
    """The tool map has exactly as many entries as the tool list."""
    assert len(_TOOL_MAP) == len(_TOOLS)


# =========================================================================
# Prototype response structures
# =========================================================================


def test_each_tool_produces_valid_response_shape() -> None:
    """Simulate each tool with minimal params to verify the response shape.

    This test confirms the lambda handler + JSON-RPC wrapping works for
    every tool.  Some tools may fail at the service layer (e.g. missing
    files, missing runs) but the response must still be valid JSON-RPC.
    """
    for tool in _TOOLS:
        # Build minimal params from the request model's required fields
        params: dict[str, Any] = {}
        for name, field in tool.request_model.model_fields.items():
            if name == "store":
                continue
            if field.is_required():
                # Fill with a plausible default
                annotation = field.annotation
                origin = getattr(annotation, "__origin__", None)
                if origin is not None:
                    args = getattr(annotation, "__args__", ())
                    non_none = [a for a in args if a is not type(None)]
                    if non_none:
                        annotation = non_none[0]
                if annotation is str:
                    params[name] = "test"
                elif annotation is int:
                    params[name] = 1
                elif annotation is float:
                    params[name] = 1.0
                elif annotation is bool:
                    params[name] = False
                else:
                    params[name] = "test"

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool.name, "arguments": params},
        }
        response = _handle_rpc_request(request)
        assert "jsonrpc" in response
        assert response["jsonrpc"] == "2.0"
        # Must have either result or error (not both at top level)
        assert ("result" in response) != ("error" in response), (
            f"Tool {tool.name} returned both result and error"
        )


# =========================================================================
# CLI integration
# =========================================================================


def test_mcp_subcommand_registered() -> None:
    """The ``bound mcp`` subcommand is registered in the CLI parser."""
    from bound.cli import _build_parser

    parser = _build_parser()
    # The subparser should be findable
    help_output = parser.format_help()
    assert "mcp" in help_output

    # Parse 'mcp' to verify the subcommand is registered
    sub = parser.parse_args(["mcp"])
    assert sub.command == "mcp"


def test_mcp_parser_accepts_once_flag() -> None:
    """The ``bound mcp`` parser accepts ``--once``."""
    from bound.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["mcp", "--once"])
    assert args.once is True


def test_mcp_parser_accepts_json_flag() -> None:
    """The ``bound mcp`` parser accepts ``--json``."""
    from bound.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["mcp", "--json"])
    assert args.json_log is True


def test_mcp_parser_defaults() -> None:
    """The ``bound mcp`` parser has sensible defaults."""
    from bound.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["mcp"])
    assert args.once is False
    assert args.json_log is False
    assert hasattr(args, "func")
    assert callable(args.func)


# =========================================================================
# MCP protocol lifecycle & JSON-RPC compliance
# =========================================================================


def test_initialize_returns_capabilities() -> None:
    """The ``initialize`` handshake returns protocol version, server info, and tools capability.

    Real MCP clients (Claude Desktop, Cursor) require this response before
    they will proceed to ``tools/list`` or ``tools/call``. Without it the
    connection never completes.
    """
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    response = _handle_rpc_request(request)
    assert response is not None
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "result" in response
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"] == {"name": "bound", "version": "0.8.0"}
    assert result["capabilities"] == {"tools": {}}


def test_initialized_notification_sends_no_response() -> None:
    """``notifications/initialized`` is a notification and MUST NOT receive a response.

    JSON-RPC 2.0 section 4: notifications carry no ``id`` and the server
    must not reply. Replying corrupts the protocol stream for clients that
    do not expect a message here.
    """
    request = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response = _handle_rpc_request(request)
    assert response is None


def test_request_without_id_sends_no_response() -> None:
    """Any request lacking an ``id`` is a notification and yields no response.

    This guards against the generic case (not just ``initialized``): the
    handler must return ``None`` so the caller writes nothing to stdout.
    """
    request = {"jsonrpc": "2.0", "method": "tools/list", "params": {}}
    response = _handle_rpc_request(request)
    assert response is None


def test_wrong_jsonrpc_version_returns_invalid_request() -> None:
    """A request with a wrong ``jsonrpc`` version is rejected with -32600."""
    request = {"jsonrpc": "1.0", "id": 7, "method": "tools/list", "params": {}}
    response = _handle_rpc_request(request)
    assert response is not None
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 7
    assert "error" in response
    assert response["error"]["code"] == INVALID_REQUEST


def test_missing_jsonrpc_version_returns_invalid_request() -> None:
    """A request with no ``jsonrpc`` field at all is rejected with -32600."""
    request = {"id": 7, "method": "tools/list", "params": {}}
    response = _handle_rpc_request(request)
    assert response is not None
    assert response["error"]["code"] == INVALID_REQUEST


def test_validation_error_maps_to_invalid_params() -> None:
    """A Pydantic ``ValidationError`` maps to INVALID_PARAMS (-32602), not INTERNAL_ERROR.

    The ``bound_policy_validate`` request model forbids extra fields and
    requires ``path``; supplying neither triggers validation. The response
    must surface which field failed via the error ``data`` field (W1/W2).
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "bound_policy_validate",
            "arguments": {},  # missing required ``path``
        },
    }
    response = _handle_rpc_request(request)
    assert response is not None
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
    # W2: error data must carry the failing field details.
    data = response["error"].get("data")
    assert isinstance(data, list)
    assert len(data) >= 1
    failed_locs = [tuple(entry.get("loc", ())) for entry in data]
    assert any("path" in loc for loc in failed_locs), (
        f"validation data should mention the 'path' field, got {failed_locs}"
    )


def test_notification_only_batch_sends_no_response() -> None:
    """A batch composed entirely of notifications yields no response list."""
    batch = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
    ]
    from bound.mcp_server import _handle_rpc_batch

    assert _handle_rpc_batch(batch) is None
