"""Tests for GenericProcessAdapter — lifecycle, events, checkpoints."""

from __future__ import annotations

import sys
import tempfile

import pytest

from bound.adapters import AdapterConfig, AdapterEvent, AgentAdapter
from bound.adapters.generic import GenericProcessAdapter

# ---------------------------------------------------------------------------
# Agent script helpers
# ---------------------------------------------------------------------------


def _write_script(script: str) -> str:
    """Write a Python script to a temp file, return its path."""
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".py", delete=False, prefix="bound_test_agent_"
    )
    tmp.write(script)
    tmp.close()
    return tmp.name


ECHO_AGENT = '''\
import sys, json
line = sys.stdin.readline()
start = json.loads(line)
print(json.dumps({
    "type": "step.completed",
    "evidence": {"pass": True},
    "candidate_id": start.get("candidate_id"),
}), flush=True)
for _ in range(100):
    cmd = json.loads(sys.stdin.readline())
    if cmd["type"] == "shutdown":
        break
    elif cmd["type"] == "checkpoint.capture":
        print(json.dumps({
            "type": "checkpoint.captured",
            "checkpoint_id": cmd.get("checkpoint_id", "cp_001"),
        }), flush=True)
    elif cmd["type"] == "checkpoint.restore":
        pass
    else:
        sys.exit(1)
'''

SLOW_AGENT = '''\
import sys, json, time
sys.stdin.readline()
time.sleep(0.5)
print(json.dumps({"type": "step.completed"}), flush=True)
for _ in range(100):
    cmd = json.loads(sys.stdin.readline())
    if cmd["type"] == "shutdown":
        break
'''

MULTI_EVENT_AGENT = '''\
import sys, json
sys.stdin.readline()
print(json.dumps({"type": "evidence.collected", "evidence": {"tests": 3}}), flush=True)
print(json.dumps({"type": "step.completed", "evidence": {"pass": True}}), flush=True)
for _ in range(100):
    cmd = json.loads(sys.stdin.readline())
    if cmd["type"] == "shutdown":
        break
'''

CRASH_AGENT = '''\
import sys
sys.stdin.readline()
sys.exit(1)
'''


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def echo_agent_path() -> str:
    """Path to the echo agent script."""
    return _write_script(ECHO_AGENT)


@pytest.fixture
def slow_agent_path() -> str:
    """Path to the slow agent script."""
    return _write_script(SLOW_AGENT)


@pytest.fixture
def multi_event_agent_path() -> str:
    """Path to the multi-event agent script."""
    return _write_script(MULTI_EVENT_AGENT)


@pytest.fixture
def crash_agent_path() -> str:
    """Path to the crash agent script."""
    return _write_script(CRASH_AGENT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGenericProcessAdapterInit:
    """Adapter initialisation."""

    def test_default_config(self) -> None:
        adapter = GenericProcessAdapter(agent_command=["echo", "hello"])
        assert adapter.config.agent_type == "generic"
        assert adapter.config.agent_command == ["echo", "hello"]
        assert adapter.config.timeout_seconds == 300.0

    def test_custom_timeout(self) -> None:
        adapter = GenericProcessAdapter(agent_command=["echo"], timeout_seconds=60.0)
        assert adapter.config.timeout_seconds == 60.0

    def test_is_agent_adapter_subclass(self) -> None:
        adapter = GenericProcessAdapter(agent_command=["echo"])
        assert isinstance(adapter, AgentAdapter)


class TestLifecycle:
    """Launch, wait, terminate round-trip tests."""

    def test_full_lifecycle(self, echo_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test task", candidate_id="cand-001")
        event = adapter.wait_for_event(timeout=5.0)
        assert event is not None
        assert event.type == "step.completed"
        assert event.evidence == {"pass": True}
        assert event.candidate_id == "cand-001"
        adapter.terminate()

    def test_terminate_idempotent(self, echo_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        adapter.terminate()
        adapter.terminate()

    def test_launch_twice_raises(self, echo_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        try:
            with pytest.raises(RuntimeError, match="already running"):
                adapter.launch(task="Second")
        finally:
            adapter.terminate()

    def test_send_command_when_not_running_raises(self) -> None:
        adapter = GenericProcessAdapter(agent_command=["echo"])
        with pytest.raises(RuntimeError, match="not running"):
            adapter.send_command({"type": "continue"})


class TestMultipleEvents:
    """Buffering multiple events."""

    def test_multiple_events_buffered(self, multi_event_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, multi_event_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        e1 = adapter.wait_for_event(timeout=5.0)
        assert e1 is not None and e1.type == "evidence.collected"
        e2 = adapter.wait_for_event(timeout=5.0)
        assert e2 is not None and e2.type == "step.completed"
        adapter.terminate()


class TestTimeout:
    """Timeout behaviour."""

    def test_wait_for_event_timeout_returns_none(self, slow_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, slow_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        event = adapter.wait_for_event(timeout=0.1)
        assert event is None
        event = adapter.wait_for_event(timeout=5.0)
        assert event is not None and event.type == "step.completed"
        adapter.terminate()


class TestAgentCrash:
    """Agent crash handling."""

    def test_crash_during_wait_raises(self, crash_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, crash_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            adapter.wait_for_event(timeout=5.0)


class TestOnEventCallback:
    """Streaming callback."""

    def test_callback_invoked(self, multi_event_agent_path: str) -> None:
        received: list[AdapterEvent] = []

        def _cb(event: AdapterEvent) -> None:
            received.append(event)

        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, multi_event_agent_path],
            timeout_seconds=10.0,
        )
        adapter.on_event = _cb
        adapter.launch(task="Test")
        adapter.wait_for_event(timeout=5.0)
        adapter.wait_for_event(timeout=5.0)
        assert len(received) == 2
        adapter.terminate()

    def test_callback_exception_does_not_crash(self, echo_agent_path: str) -> None:
        def _raise(event: AdapterEvent) -> None:
            raise RuntimeError("boom")

        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.on_event = _raise
        adapter.launch(task="Test")
        event = adapter.wait_for_event(timeout=5.0)
        assert event is not None
        adapter.terminate()


class TestCheckpoints:
    """Checkpoint capture/restore for GenericProcessAdapter."""

    def test_capture_returns_dict(self, echo_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        cp = adapter.capture_checkpoint()
        assert "checkpoint_id" in cp
        assert "timestamp" in cp
        assert "source" in cp
        adapter.terminate()

    def test_capture_when_not_running_raises(self) -> None:
        adapter = GenericProcessAdapter(agent_command=["echo"])
        with pytest.raises(RuntimeError, match="not running"):
            adapter.capture_checkpoint()

    def test_restore_checkpoint(self, echo_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        cp = adapter.capture_checkpoint()
        adapter.restore_checkpoint(cp["checkpoint_id"])
        adapter.terminate()

    def test_restore_unknown_raises_keyerror(self, echo_agent_path: str) -> None:
        adapter = GenericProcessAdapter(
            agent_command=[sys.executable, echo_agent_path],
            timeout_seconds=10.0,
        )
        adapter.launch(task="Test")
        with pytest.raises(KeyError, match="not found"):
            adapter.restore_checkpoint("nonexistent")
        adapter.terminate()

    def test_restore_when_not_running_raises(self) -> None:
        adapter = GenericProcessAdapter(agent_command=["echo"])
        with pytest.raises(RuntimeError, match="not running"):
            adapter.restore_checkpoint("any")


class TestAdapterConfigModel:
    """AdapterConfig validation."""

    def test_defaults(self) -> None:
        cfg = AdapterConfig(agent_command=["echo"])
        assert cfg.agent_type == "generic"
        assert cfg.timeout_seconds == 300.0

    def test_empty_command_raises(self) -> None:
        with pytest.raises(ValueError):
            AdapterConfig(agent_command=[])

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError):
            AdapterConfig(agent_command=["echo"], timeout_seconds=-1.0)


class TestAdapterEventModel:
    """AdapterEvent model."""

    def test_minimal(self) -> None:
        ev = AdapterEvent(type="task.started")
        assert ev.type == "task.started"
        assert ev.timestamp is not None

    def test_extra_fields_allowed(self) -> None:
        ev = AdapterEvent(type="custom.event", my_field=42)
        assert ev.my_field == 42
