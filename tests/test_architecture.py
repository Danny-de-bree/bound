"""Architecture / dependency-hygiene tests for the BOUND v0.1 core.

The BOUND core is contractually:

* driven by a deterministic :class:`StaticEvaluator` (no live evaluator needed);
* network-free — it must reach a decision fully offline;
* API-key-free — no credentials are required to evaluate an action;
* free of any LLM / provider SDK in its installed dependencies.

These invariants are part of the project's "Final test requirements" under the
*Architecture* heading. They are asserted directly here rather than inferred from
the happy-path tests, so a future regression that silently introduces a network
call, an API-key read, or a provider dependency into the core is caught loudly.

The checks combine three complementary strategies:

1. **Dependency metadata** — the installed ``bound`` distribution's requirements
   must not list any known LLM / provider SDK.
2. **Static import scan** — the ``bound`` package source must not import any
   networking or provider module (parsed with :mod:`ast`, so comments and
   strings cannot produce false positives).
3. **Runtime** — running the policy and the CLI with a sanitized environment
   and a blocked socket must still produce the deterministic decision, proving
   no network access or API key is actually exercised at runtime.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import socket
import sys
from pathlib import Path

import pytest

from bound.cli import main
from bound.evaluator import StaticEvaluator
from bound.models import Action, BoundCriteria, EvaluationScores
from bound.policy import BoundPolicy

# The exact ``bound evaluate`` invocation from the project's definition-of-done.
_DOD_ARGS = [
    "evaluate",
    "--action", "Book the direct flight",
    "--goal", "Travel from Paris to New York",
    "--acceptance", "0.9",
    "--influence", "0.2",
    "--risk", "0.1",
    "--cost", "0.2",
    "--weight", "1.0",
    "--threshold", "0.6",
]

#: Root of the installed ``bound`` package source tree.
_SRC_ROOT = Path(__import__("bound").__file__).resolve().parent

#: Known LLM / provider SDKs that must never be a (runtime) dependency of BOUND.
_FORBIDDEN_PROVIDER_PACKAGES = frozenset(
    {
        "openai",
        "anthropic",
        "google-generativeai",
        "google-genai",
        "vertexai",
        "langchain",
        "langchain-core",
        "langchain-openai",
        "langchain-anthropic",
        "llama-cpp-python",
        "transformers",
        "cohere",
        "mistralai",
        "deepseek",
        "ollama",
        "replicate",
        "together",
        "huggingface-hub",
    },
)

#: Top-level importable module names whose presence in the BOUND source would
#: indicate network access or a provider coupling. Stdlib networking primitives
#: are included so a regression cannot sneak in via ``socket`` or ``urllib``.
_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "socket",
        "urllib",
        "http",
        "httpx",
        "requests",
        "aiohttp",
        "httpcore",
        "websockets",
        "websocket",
        "openai",
        "anthropic",
        "google",
        "google-generativeai",
        "google-genai",
        "vertexai",
        "langchain",
        "llama-cpp-python",
        "transformers",
        "cohere",
        "mistralai",
        "deepseek",
        "ollama",
        "replicate",
        "together",
        "huggingface-hub",
    },
)

#: Environment variables that would signal an API-key-based provider is expected.
_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
    "COHERE_API_KEY",
    "MISTRAL_API_KEY",
    "REPLICATE_API_TOKEN",
    "TOGETHER_API_KEY",
    "HUGGINGFACEHUB_API_TOKEN",
    "VERTEX_API_KEY",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_requirement_name(requirement: str) -> str:
    """Extract the canonical, lowercased package name from a requirement string.

    PEP 508 requirement strings carry version specifiers, markers and extras
    (e.g. ``"pydantic>=2.0"``, ``"package[extra]>=1.0; python_version<'3.10'"``).
    We only care about the bare distribution name, so everything from the first
    specifier/extras/marker character onward is discarded.

    Args:
        requirement: A raw requirement string from distribution metadata.

    Returns:
        The normalized package name (lowercased, underscores to hyphens), with
        no version, extras, or environment markers.
    """
    name = requirement
    for sep in ("[", "<", ">", "=", "!", "~", ";", " "):
        name = name.split(sep, 1)[0]
    return name.strip().lower().replace("_", "-")


def _module_roots_imported_by(source_path: Path) -> set[str]:
    """Return the top-level module names imported by a Python source file.

    Parses ``source_path`` with :mod:`ast` and collects the root of every
    ``import`` and ``from ... import`` statement. Relative imports
    (``from . import``) are skipped because they are intra-package, not external
    dependencies. Using the AST avoids false positives from module names
    appearing in comments or string literals.

    Args:
        source_path: Path to a ``.py`` file to scan.

    Returns:
        A set of top-level imported module names (e.g. ``{"json", "pydantic"}``).
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # Absolute imports only (relative ``from . import`` are intra-package).
            roots.add(node.module.split(".")[0])
    return roots


# ---------------------------------------------------------------------------
# No LLM / provider SDK installed
# ---------------------------------------------------------------------------


def test_bound_distribution_declares_no_provider_sdk() -> None:
    """The installed ``bound`` distribution must not require any LLM SDK.

    Guards the model-agnostic invariant at the dependency level: if a provider
    SDK (OpenAI, Anthropic, LangChain, ...) ever became a runtime requirement,
    the BOUND core would stop being provider-agnostic. We read the distribution
    metadata rather than the lockfile so the check reflects what is actually
    installed, not just what is declared.
    """
    # Resolve the distribution that ships the ``bound`` import package.
    # The PyPI distribution name (``bound-policy``) and the import name
    # (``bound``) intentionally differ, and editable/src-layout installs do not
    # always populate ``packages_distributions()``, so we discover the
    # distribution by finding the one whose files include the ``bound`` package.
    bound_file = Path(__import__("bound").__file__).resolve()
    bound_root = bound_file.parent
    requires: list[str] = []
    for dist in importlib.metadata.distributions():
        for located in dist.files or []:
            if bound_root.joinpath(located).resolve() == bound_file:
                requires = dist.requires or []
                break
        if requires:
            break
    assert requires is not None, "could not locate the distribution shipping the 'bound' package"
    declared = {_canonical_requirement_name(req) for req in requires}

    offenders = declared & _FORBIDDEN_PROVIDER_PACKAGES
    assert not offenders, (
        f"BOUND core must not depend on any LLM/provider SDK; found: {sorted(offenders)}"
    )


def test_importing_bound_does_not_load_any_provider_sdk() -> None:
    """Importing ``bound`` must not load any provider SDK into ``sys.modules``.

    A runtime complement to the metadata check: even if a SDK were an optional,
    undeclared dependency, importing the core should not pull it in. We assert
    that none of the forbidden provider packages are present in ``sys.modules``
    after importing ``bound``.
    """
    import bound  # noqa: F401  (import side-effect under test)

    loaded = set(sys.modules)
    offenders = {
        name
        for name in loaded
        if name.split(".")[0].lower().replace("_", "-") in _FORBIDDEN_PROVIDER_PACKAGES
    }
    assert not offenders, (
        f"Importing bound loaded forbidden provider modules: {sorted(offenders)}"
    )
    assert bound.__version__ == "0.1.0"


# ---------------------------------------------------------------------------
# No network required
# ---------------------------------------------------------------------------


def test_bound_source_imports_no_network_or_provider_modules() -> None:
    """The BOUND package source must not import any networking/provider module.

    A static, AST-based scan of every ``.py`` file under ``src/bound``. This is
    the most direct guard for "no network required": if the core never imports a
    networking primitive or provider client, it cannot make a network call.
    Parsing the AST (rather than grepping) avoids false positives from names
    mentioned in docstrings or comments.
    """
    offenders: dict[str, set[str]] = {}
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        forbidden = _module_roots_imported_by(path) & _FORBIDDEN_IMPORT_ROOTS
        if forbidden:
            offenders[str(path.relative_to(_SRC_ROOT))] = forbidden

    assert not offenders, (
        "BOUND core must not import networking/provider modules; "
        f"found: {offenders}"
    )


def test_policy_reaches_decision_with_socket_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The policy reaches a deterministic decision with the socket primitive blocked.

    If the core attempted any network access it would need a socket; patching
    ``socket.socket`` (and ``create_connection``) to raise guarantees that no
    connection is opened while evaluating the flight example. The decision must
    still be the deterministic ``ACCEPT`` with ``S = 0.8``.
    """

    def _no_network(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("BOUND core attempted a network connection")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    scores = EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    action = Action(description="Book the direct flight", goal="Travel from Paris to New York")
    criteria = BoundCriteria(weight=1.0, threshold=0.6)

    result = BoundPolicy(StaticEvaluator(scores)).evaluate(action, criteria)

    assert result.score == pytest.approx(0.8, abs=1e-12)
    assert result.decision == "ACCEPT"


def test_cli_runs_with_socket_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI produces its JSON + prompt with the socket primitive blocked.

    Extends the socket-blocking guard across the full CLI path (argparse then
    policy then JSON/prompt emission). Nothing in the v0.1 CLI may require
    network access, so blocking the socket must not prevent a successful
    evaluation.
    """

    def _no_network(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("BOUND CLI attempted a network connection")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    rc = main(_DOD_ARGS)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "ACCEPT"
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)


# ---------------------------------------------------------------------------
# No API key required
# ---------------------------------------------------------------------------


def test_cli_runs_without_any_api_key_in_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI must reach a decision with every API-key variable unset.

    The BOUND v0.1 core must not require credentials of any kind. We delete all
    common provider API-key environment variables before running the
    definition-of-done invocation, then assert it still returns the deterministic
    ``ACCEPT`` decision. This proves the core does not gate on a key being
    present.
    """
    for var in _API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    rc = main(_DOD_ARGS)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "ACCEPT"
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)


def test_cli_ignores_present_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-set API keys must be ignored — the decision stays deterministic.

    Belt-and-braces to the previous test: even when provider API keys *are*
    present in the environment, the core must not use them and the result must
    remain the deterministic ``ACCEPT``. This guards against a future change that
    opportunistically reads a key.
    """
    for var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(var, "unused-dummy-key")

    rc = main(_DOD_ARGS)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "ACCEPT"
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)


# ---------------------------------------------------------------------------
# Policy works with StaticEvaluator (architecture integration)
# ---------------------------------------------------------------------------


def test_policy_with_static_evaluator_is_deterministic_and_offline() -> None:
    """A StaticEvaluator-backed policy yields a reproducible offline decision.

    This is the architecture requirement stated positively: the full pipeline
    (Evaluator then Calculator then Policy) runs with :class:`StaticEvaluator`,
    needs no network/API key/SDK, and returns the canonical ``S = 0.8`` /
    ``ACCEPT`` result every time it is invoked.
    """
    scores = EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    action = Action(description="Book the direct flight", goal="Travel from Paris to New York")
    criteria = BoundCriteria(weight=1.0, threshold=0.6)

    first = BoundPolicy(StaticEvaluator(scores)).evaluate(action, criteria)
    second = BoundPolicy(StaticEvaluator(scores)).evaluate(action, criteria)

    assert first.score == pytest.approx(0.8, abs=1e-12)
    assert first.decision == "ACCEPT"
    assert first == second

