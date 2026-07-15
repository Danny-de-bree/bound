"""Architecture / dependency-hygiene tests for the BOUND v0.2 core.

The BOUND core is contractually:

* driven by a deterministic evaluator — the manual :class:`StaticEvaluator`
  (direct-score mode) and the v0.2 :class:`CodingWorkflowEvaluator`
  (workflow-signal mode) both reach a decision fully offline;
* network-free — it must reach a decision fully offline, including the new
  workflow evaluator and the experiment harness that replays trajectories;
* API-key-free — no credentials are required to evaluate an action or to
  replay an experiment;
* free of any LLM / provider SDK in its installed dependencies or imports.

These invariants are part of the project's "Final test requirements" under the
*Architecture* heading. They are asserted directly here rather than inferred
from the happy-path tests, so a future regression that silently introduces a
network call, an API-key read, or a provider dependency into the core —
including the v0.2 ``workflow.py`` and ``experiment.py`` modules — is caught
loudly.

The checks combine three complementary strategies:

1. **Dependency metadata** — the installed ``bound`` distribution's requirements
   must not list any known LLM / provider SDK.
2. **Static import scan** — the ``bound`` package source (including the v0.2
   ``workflow.py`` and ``experiment.py`` modules) must not import any
   networking or provider module (parsed with :mod:`ast`, so comments and
   strings cannot produce false positives).
3. **Runtime** — running the policy, the CLI (both ``evaluate`` and
   ``evaluate-workflow``), the :class:`CodingWorkflowEvaluator`, the
   experiment harness, and the v0.3 contract workflow
   (:class:`BoundWorkflow` + :class:`ContractEvaluator`) with a sanitized
   environment and a blocked socket must still produce the deterministic
   decision, proving no network access or API key is actually exercised at
   runtime.

v0.3 extends the same invariants to the contract pipeline
(:mod:`bound.contracts`, :mod:`bound.evidence`,
:mod:`bound.contract_evaluator`, :mod:`bound.bound_workflow`,
:mod:`bound.contract_quality`, :mod:`bound.llm_adapters`). The LLM-backed
contract generator is an *optional* convenience layer; the deterministic
core must reach an ``ACCEPT`` purely from a :class:`StaticContractGenerator`
+ :class:`ContractEvaluator` + :class:`BoundPolicy` with simulated
:class:`ExecutionEvidence` — no LLM, no network, no API key — which is the
project's Definition of Done for v0.3.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import socket
import sys
from pathlib import Path

import pytest

from bound.bound_workflow import BoundWorkflow
from bound.cli import main
from bound.contract_evaluator import ContractEvaluator
from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    StaticContractGenerator,
    StepContract,
)
from bound.evaluator import StaticEvaluator
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.experiment import run_experiment
from bound.models import (
    Action,
    AgentStep,
    AgentTrajectory,
    BoundCriteria,
    CodingWorkflowSignals,
    EvaluationScores,
)
from bound.policy import BoundPolicy
from bound.workflow import CodingWorkflowEvaluator

# The exact ``bound evaluate`` invocation from the project's definition-of-done.
# Uses the deprecated scalar ``--weight`` alias; the CLI folds it into the
# symmetric ``weights.acceptance`` (all other weights default to ``1.0``), so
# the v0.1-equivalent score ``S = 0.8`` and ``ACCEPT`` decision are preserved.
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

# A ``bound evaluate-workflow`` invocation that derives scores from workflow
# signals (no manual A/I/R/C) and still reaches ``ACCEPT``. Used to prove the
# v0.2 deterministic coding-workflow evaluator path is also network-free. With
# all gates green, zero cost and a clean rollback, A=1.0, R=0.0, C=0.0, I=0.0,
# so S=1.0 >= T=0.6 -> ACCEPT.
_DOD_WORKFLOW_ARGS = [
    "evaluate-workflow",
    "--action", "Implement feature X",
    "--goal", "Complete issue #123",
    "--test-pass-rate", "1.0",
    "--lint-passed",
    "--type-check-passed",
    "--required-checks-passed", "1.0",
    "--rollback-available",
    "--retry-count", "0",
    "--tool-call-count", "0",
    "--threshold", "0.6",
]

# Fields the auditable CLI JSON payload must expose for the v0.2 contract.
# The DoD invocation runs in direct-score (``evaluate``) mode, where
# ``provenance`` is intentionally absent (it is workflow-only), so this set
# pins exactly the direct-score payload shape — including the new v0.2
# ``weights`` and ``distance_to_threshold`` fields.
_DOD_JSON_FIELDS = {
    "scores",
    "weights",
    "weight",
    "threshold",
    "retry_margin",
    "rollback_risk_threshold",
    "acceptance_component",
    "influence_component",
    "risk_component",
    "cost_component",
    "score",
    "distance_to_threshold",
    "decision",
}

#: v0.2 source modules that the architecture guards must provably cover. If a
#: future change adds another offline evaluator / harness module it must be
#: registered here so the forbidden-import scan and runtime guards keep pace.
_V0_2_SOURCE_MODULES = ("workflow.py", "experiment.py")

#: v0.3 contract-pipeline source modules that the architecture guards must
#: provably cover. The contract layer is the load-bearing v0.3 addition; if a
#: module is removed or renamed the forbidden-import scan and runtime guards
#: would silently stop covering it, so we fail loudly here instead. This is the
#: registration point for the v0.3 modules listed in the project's Phase 16
#: spec and Definition of Done.
_V0_3_SOURCE_MODULES = (
    "contracts.py",
    "evidence.py",
    "contract_evaluator.py",
    "bound_workflow.py",
    "contract_quality.py",
    "llm_adapters.py",
)

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


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``socket.socket`` and ``socket.create_connection`` to raise.

    Used by the runtime network-free guards so any attempt to open a
    connection — from the policy, the CLI, the workflow evaluator, or the
    experiment harness — surfaces as a loud ``AssertionError`` rather than a
    silent (and flaky) network call.

    Args:
        monkeypatch: The pytest fixture used to install the patches.
    """

    def _no_network(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("BOUND core attempted a network connection")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)


def _wipe_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every common provider API-key environment variable.

    Companion to :func:`_block_sockets` for the runtime API-key-free guards:
    it makes the absence of credentials explicit so a workflow that *did* gate on
    a key fails loudly rather than opportunistically reading a real one.

    Args:
        monkeypatch: The pytest fixture used to delete the variables.
    """
    for var in _API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


#: All-zero :class:`EvaluationScores` used as the vestigial policy placeholder
#: in the v0.3 contract workflow. ``BoundWorkflow.evaluate_step`` rebinds the
#: policy's evaluator per call to a :class:`StaticEvaluator` of the *contract*
#: scores, so these scores are never used to score a step — they merely
#: satisfy the :class:`BoundPolicy` constructor (mirroring
#: ``test_bound_workflow.py``).
_V0_3_ZERO_SCORES = EvaluationScores(acceptance=0.0, influence=0.0, risk=0.0, cost=0.0)


def _green_contract_workflow() -> tuple[BoundWorkflow, StepContract, ExecutionEvidence]:
    """Build the deterministic all-green v0.3 contract workflow fixture.

    Constructs a :class:`BoundWorkflow` wired with a
    :class:`StaticContractGenerator` (one step, two required acceptance checks,
    no risk checks, no budget) plus a :class:`ContractEvaluator` and a
    :class:`BoundPolicy`. The companion :class:`ExecutionEvidence` records both
    required checks passing, a clean rollback and no unexpected artefacts.

    This is the v0.3 Definition-of-Done fixture: with default weights
    (``W_A=W_I=W_R=W_C=1.0``) the :class:`ContractEvaluator` yields
    ``A=1.0, I=0.0, R=0.0, C=0.0`` so ``S = 1.0``; with ``threshold=0.6`` the
    deterministic policy returns ``ACCEPT``. No LLM, network, or API key is
    involved anywhere in the computation.

    Returns:
        A ``(workflow, contract, evidence)`` triple that deterministically
        reaches ``ACCEPT`` with ``S == 1.0``.
    """
    contract = StepContract(
        id="ship",
        description="Ship the parser",
        goal="Cover the parser edge cases",
        acceptance_checks=[
            AcceptanceCheck(id="tests-pass", description="All unit tests pass"),
            AcceptanceCheck(id="lint-pass", description="The linter is clean"),
        ],
    )
    plan = BoundPlan(goal="Ship the parser", steps=[contract])
    workflow = BoundWorkflow(
        StaticContractGenerator(plan),
        ContractEvaluator(),
        BoundPolicy(StaticEvaluator(_V0_3_ZERO_SCORES)),
    )
    evidence = ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="tests-pass", passed=True, source="pytest"),
            CheckEvidence(check_id="lint-pass", passed=True, source="ruff"),
        ],
        rollback_available=True,
    )
    return workflow, contract, evidence


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
    assert bound.__version__ == "0.3.0"


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


# ---------------------------------------------------------------------------
# v0.2 module coverage of the forbidden-import scan
# ---------------------------------------------------------------------------


def test_v0_2_source_modules_exist_and_are_scanned() -> None:
    """The v0.2 source modules must be present and covered by the import scan.

    The forbidden-import scan walks ``_SRC_ROOT.rglob("*.py")``, so any new
    module is included automatically — *provided it exists*. This test makes
    that coverage explicit for ``workflow.py`` and ``experiment.py``: if either
    is removed or renamed, the architecture guards that follow would silently
    stop covering it, so we fail loudly here instead. This is the registration
    point the ``_V0_2_SOURCE_MODULES`` constant exists to enforce.
    """
    scanned = {path.name for path in _SRC_ROOT.rglob("*.py")}
    missing = [name for name in _V0_2_SOURCE_MODULES if name not in scanned]
    assert not missing, f"v0.2 source modules missing from src/bound: {missing}"


@pytest.mark.parametrize("module_name", _V0_2_SOURCE_MODULES)
def test_v0_2_module_imports_no_network_or_provider_modules(module_name: str) -> None:
    """Each v0.2 source module must import no networking/provider module.

    A focused, per-module companion to the package-wide scan. Pinning each
    module individually keeps a regression localised: if ``experiment.py``
    ever pulled in ``requests`` while ``workflow.py`` stayed clean, this test
    would name the offender directly rather than reporting the whole package.
    """
    path = _SRC_ROOT / module_name
    assert path.exists(), f"{module_name} not found under {_SRC_ROOT}"

    forbidden = _module_roots_imported_by(path) & _FORBIDDEN_IMPORT_ROOTS
    assert not forbidden, (
        f"{module_name} must not import networking/provider modules; found: {forbidden}"
    )


# ---------------------------------------------------------------------------
# v0.3 module coverage of the forbidden-import scan
# ---------------------------------------------------------------------------


def test_v0_3_source_modules_exist_and_are_scanned() -> None:
    """The v0.3 contract-pipeline modules must be present and scanned.

    Mirrors :func:`test_v0_2_source_modules_exist_and_are_scanned` for the six
    v0.3 modules listed in the Phase 16 spec
    (``contracts.py`` / ``evidence.py`` / ``contract_evaluator.py`` /
    ``bound_workflow.py`` / ``contract_quality.py`` / ``llm_adapters.py``).
    The package-wide AST scan walks ``_SRC_ROOT.rglob(\"*.py\")`` so it picks
    them up automatically, *provided they exist* — if one is removed or
    renamed the forbidden-import and runtime guards would silently stop
    covering it, so this test fails loudly at the registration point the
    ``_V0_3_SOURCE_MODULES`` constant exists to enforce.
    """
    scanned = {path.name for path in _SRC_ROOT.rglob("*.py")}
    missing = [name for name in _V0_3_SOURCE_MODULES if name not in scanned]
    assert not missing, f"v0.3 source modules missing from src/bound: {missing}"


@pytest.mark.parametrize("module_name", _V0_3_SOURCE_MODULES)
def test_v0_3_module_imports_no_network_or_provider_modules(module_name: str) -> None:
    """Each v0.3 contract-pipeline module must import no networking/provider module.

    A focused, per-module companion to the package-wide scan. The v0.3
    contract layer is the *load-bearing* addition for the Definition of Done
    (``natural-language plan -> contracts -> evidence -> automatic A/I/R/C ->
    deterministic decision``), so a regression that sneaks a network primitive
    or an LLM SDK into — say — ``contract_evaluator.py`` would break the
    ``no mandatory LLM dependency`` / ``no network required`` invariants in
    exactly the module that must stay deterministic. Pinning each module
    individually keeps such a regression localised and named.
    """
    path = _SRC_ROOT / module_name
    assert path.exists(), f"{module_name} not found under {_SRC_ROOT}"

    forbidden = _module_roots_imported_by(path) & _FORBIDDEN_IMPORT_ROOTS
    assert not forbidden, (
        f"{module_name} must not import networking/provider modules; found: {forbidden}"
    )


def test_llm_adapters_module_is_import_free() -> None:
    """The optional-adapter placeholder module must perform zero imports.

    ``bound.llm_adapters`` is the documented seam where an LLM-backed
    :class:`ContractGenerator` *would* live, and the Phase 4 boundary demands
    it ship import-free so importing ``bound`` can never pull a provider SDK.
    Asserting the parsed AST contains no ``import`` / ``from ... import`` node
    at all is stronger than the forbidden-root scan: it pins the documented
    "documentation placeholder" contract, so a future contributor adding even a
    stdlib import there is forced to reconsider whether their code belongs in the
    optional package instead of the core.
    """
    path = _SRC_ROOT / "llm_adapters.py"
    assert path.exists(), f"llm_adapters.py not found under {_SRC_ROOT}"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    import_nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not import_nodes, (
        "bound.llm_adapters must remain import-free (the optional-LLM seam); "
        f"found import nodes: {ast.dump(import_nodes[0]) if import_nodes else None}"
    )


def test_importing_bound_submodules_loads_no_provider_sdk() -> None:
    """Importing every public BOUND submodule loads no provider SDK.

    ``import bound`` only triggers :mod:`bound.models`; the heavier modules
    (``workflow``, ``experiment``, ``cli`` and the v0.3 contract modules
    ``contracts``, ``evidence``, ``contract_evaluator``, ``bound_workflow``,
    ``contract_quality``, ``llm_adapters``) are imported on demand. We import
    each one explicitly and then assert none of the forbidden provider packages
    leaked into ``sys.modules`` — guarding against a submodule that lazily
    pulls a provider client at import time, including the v0.3 optional-LLM seam.
    """
    import bound.bound_workflow  # noqa: F401  (import side-effect under test)
    import bound.cli  # noqa: F401
    import bound.contract_evaluator  # noqa: F401
    import bound.contract_quality  # noqa: F401
    import bound.contracts  # noqa: F401
    import bound.evidence  # noqa: F401
    import bound.experiment  # noqa: F401
    import bound.llm_adapters  # noqa: F401
    import bound.workflow  # noqa: F401

    loaded = set(sys.modules)
    offenders = {
        name
        for name in loaded
        if name.split(".")[0].lower().replace("_", "-") in _FORBIDDEN_PROVIDER_PACKAGES
    }
    assert not offenders, (
        f"Importing BOUND submodules loaded forbidden provider modules: {sorted(offenders)}"
    )


# ---------------------------------------------------------------------------
# CLI payload contract for v0.2 (weights + distance_to_threshold)
# ---------------------------------------------------------------------------


def test_cli_dod_payload_exposes_v0_2_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The DoD CLI payload must expose the v0.2 auditability fields.

    The architecture invariant is not only "no network" but also "auditable":
    a consumer reading only the JSON must reconstruct ``S`` from the symmetric
    ``weights`` and the four scores, and read the signed
    ``distance_to_threshold``. This pins the exact direct-score payload shape
    (including the new v0.2 fields) so a future change that drops a field is
    caught at the architecture gate, not by a downstream consumer.
    """
    rc = main(_DOD_ARGS)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert set(payload) == _DOD_JSON_FIELDS
    # The four symmetric weights are present and reproduce v0.1 defaults.
    assert payload["weights"] == {
        "acceptance": 1.0,
        "influence": 1.0,
        "risk": 1.0,
        "cost": 1.0,
    }
    # The deprecated scalar alias tracks weights.acceptance.
    assert payload["weight"] == payload["weights"]["acceptance"]
    # The signed distance matches S - T and is positive for an ACCEPT.
    assert payload["distance_to_threshold"] == pytest.approx(
        payload["score"] - payload["threshold"], abs=1e-12
    )
    assert payload["distance_to_threshold"] > 0


# ---------------------------------------------------------------------------
# v0.2 deterministic evaluators are network-free at runtime
# ---------------------------------------------------------------------------


def test_coding_workflow_evaluator_reaches_decision_with_socket_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodingWorkflowEvaluator reaches a deterministic decision offline.

    The v0.2 workflow evaluator derives ``A/I/R/C`` from in-process signals;
    blocking the socket primitive must not prevent a decision. This extends the
    runtime network-free guard from the manual :class:`StaticEvaluator` to the
    deterministic coding-workflow evaluator, proving the new evaluator path is
    genuinely offline, not merely untested.
    """
    _block_sockets(monkeypatch)

    signals = CodingWorkflowSignals(
        test_pass_rate=1.0,
        lint_passed=True,
        type_check_passed=True,
        required_checks_passed=1.0,
        rollback_available=True,
        retry_count=0,
        tool_call_count=0,
    )
    action = Action(description="Implement feature X", goal="Complete issue #123")
    criteria = BoundCriteria(threshold=0.6)

    first = BoundPolicy(CodingWorkflowEvaluator(signals)).evaluate(action, criteria)
    second = BoundPolicy(CodingWorkflowEvaluator(signals)).evaluate(action, criteria)

    # All four completion gates green -> evidence_breadth = 1.0 -> A=1.0,
    # R=0.0, C=0.0, I=0.0 -> S=1.0 >= T=0.6 -> ACCEPT.
    assert first.score == pytest.approx(1.0, abs=1e-12)
    assert first.decision == "ACCEPT"
    assert first == second


def test_experiment_harness_runs_with_socket_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The experiment harness replays a trajectory fully offline.

    The harness builds a :class:`CodingWorkflowEvaluator` per step and runs the
    deterministic policy; with the socket primitive blocked it must still
    produce a reproducible result. This guards the v0.2 "evidence" surface —
    the experiment that shows where BOUND stops a trajectory must not depend
    on any network access.
    """
    _block_sockets(monkeypatch)

    trajectory = AgentTrajectory(
        task_id="arch-offline",
        steps=[
            AgentStep(
                step_index=0,
                signals=CodingWorkflowSignals(
                    test_pass_rate=0.0, lint_passed=False, rollback_available=True
                ),
            ),
            AgentStep(
                step_index=1,
                signals=CodingWorkflowSignals(
                    test_pass_rate=1.0,
                    required_checks_passed=1.0,
                    lint_passed=True,
                    type_check_passed=True,
                    rollback_available=True,
                ),
            ),
        ],
        actual_stop_step=1,
    )
    criteria = BoundCriteria(threshold=0.6)

    first = run_experiment(trajectory, criteria)
    second = run_experiment(trajectory, criteria)

    assert first.accepted is True
    assert first.bound_stop_step == 1
    assert first.steps_saved == 0
    assert first.model_dump() == second.model_dump()


def test_cli_evaluate_workflow_runs_with_socket_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``evaluate-workflow`` CLI path produces its JSON with sockets blocked.

    Extends the socket-blocking guard across the full workflow CLI path
    (argparse, CodingWorkflowEvaluator, policy, JSON+prompt emission). Nothing
    in the v0.2 workflow subcommand may require network access.
    """
    _block_sockets(monkeypatch)

    rc = main(_DOD_WORKFLOW_ARGS)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "ACCEPT"
    assert payload["score"] == pytest.approx(1.0, abs=1e-12)
    # Workflow mode carries per-dimension provenance that direct-score lacks.
    assert set(payload["provenance"]) == {"acceptance", "influence", "risk", "cost"}


def test_cli_evaluate_workflow_runs_without_any_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The workflow CLI must not require credentials of any kind.

    Belt-and-braces for the v0.2 evaluator path: deleting every common provider
    API-key variable before running ``evaluate-workflow`` must still yield the
    deterministic ``ACCEPT``. The workflow evaluator is signal-driven, so no key
    can gate it.
    """
    for var in _API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    rc = main(_DOD_WORKFLOW_ARGS)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "ACCEPT"
    assert payload["score"] == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# All four v0.2 decisions are meaningfully reachable (Definition of Done)
# ---------------------------------------------------------------------------


def test_all_four_decisions_are_reachable_offline() -> None:
    """ACCEPT, RETRY, REPLAN and ROLLBACK are each reachable, fully offline.

    This is the Definition-of-Done gate stated as an architecture test: the
    four decisions are not just present in the enum, they are each produced by
    the deterministic policy for concrete, reproducible inputs — with no
    network and no API key. A future change that makes any decision unreachable
    (the v0.1 ``REPLAN`` float-equality trap) fails here.
    """
    action = Action(description="Do the thing", goal="Achieve the goal")
    decisions: set[str] = set()

    # ROLLBACK: unsafe risk crosses the safety boundary before the threshold.
    decisions.add(
        BoundPolicy(
            StaticEvaluator(
                EvaluationScores(acceptance=1.0, influence=0.0, risk=0.9, cost=0.0)
            )
        )
        .evaluate(action, BoundCriteria(threshold=0.6, rollback_risk_threshold=0.8))
        .decision
    )
    # ACCEPT: score exactly at the threshold (boundary-inclusive).
    decisions.add(
        BoundPolicy(
            StaticEvaluator(
                EvaluationScores(acceptance=0.6, influence=0.0, risk=0.0, cost=0.0)
            )
        )
        .evaluate(action, BoundCriteria(threshold=0.6, rollback_risk_threshold=0.8))
        .decision
    )
    # RETRY: just below threshold but within the retry margin.
    decisions.add(
        BoundPolicy(
            StaticEvaluator(
                EvaluationScores(acceptance=0.55, influence=0.0, risk=0.0, cost=0.0)
            )
        )
        .evaluate(
            action,
            BoundCriteria(threshold=0.6, retry_margin=0.1, rollback_risk_threshold=0.8),
        )
        .decision
    )
    # REPLAN: too far below the threshold to retry (fall-through).
    decisions.add(
        BoundPolicy(
            StaticEvaluator(
                EvaluationScores(acceptance=0.2, influence=0.0, risk=0.0, cost=0.0)
            )
        )
        .evaluate(
            action,
            BoundCriteria(threshold=0.6, retry_margin=0.1, rollback_risk_threshold=0.8),
        )
        .decision
    )

    assert decisions == {"ACCEPT", "RETRY", "REPLAN", "ROLLBACK"}



# ---------------------------------------------------------------------------
# v0.3 contract workflow is network-free / API-key-free at runtime
# ---------------------------------------------------------------------------


def test_bound_workflow_reaches_decision_with_socket_blocked_and_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v0.3 contract workflow reaches a deterministic decision offline.

    Extends the runtime network-free / API-key-free guards from the v0.2
    :class:`StaticEvaluator` / :class:`CodingWorkflowEvaluator` paths to the
    v0.3 contract pipeline. With the socket primitive blocked *and* every
    common provider API-key variable unset, building a
    ``BoundWorkflow(StaticContractGenerator, ContractEvaluator, BoundPolicy)``
    and running ``prepare`` + ``evaluate_step`` must still produce the
    deterministic ``ACCEPT`` with ``S == 1.0``. This proves the contract layer
    genuinely exercises no network and no credentials at runtime — not merely
    that it imports nothing, but that nothing is actually called.
    """
    _block_sockets(monkeypatch)
    _wipe_api_keys(monkeypatch)

    workflow, contract, evidence = _green_contract_workflow()

    # prepare: natural-language plan -> validated BoundPlan (the generator
    # ignores the text and returns the static plan, by identity).
    plan = workflow.prepare(goal="Ship the parser", plan="1. write tests 2. fix bugs")
    assert plan is workflow.contract_generator.plan

    criteria = BoundCriteria(threshold=0.6)
    first = workflow.evaluate_step(contract=contract, evidence=evidence, criteria=criteria)
    second = workflow.evaluate_step(contract=contract, evidence=evidence, criteria=criteria)

    # A=1.0 (2/2 required), R=0.0, C=0.0, I=0.0 -> S=1.0 >= 0.6 -> ACCEPT.
    assert first.scores.acceptance == pytest.approx(1.0, abs=1e-12)
    assert first.scores.risk == pytest.approx(0.0, abs=1e-12)
    assert first.scores.cost == pytest.approx(0.0, abs=1e-12)
    assert first.score == pytest.approx(1.0, abs=1e-12)
    assert first.decision == "ACCEPT"
    # Deterministic: the exact same inputs reproduce the exact same result.
    assert first == second
    # The contract provenance flows onto the result (the StaticEvaluator bridge
    # carries none, but evaluate_step forwards the ContractEvaluator's).
    assert set(first.provenance) == {"acceptance", "influence", "risk", "cost"}


def test_bound_workflow_decision_is_deterministic_without_llm() -> None:
    """A contract-based decision is bit-for-bit reproducible across instances.

    Intent: pin the "final decision remains deterministic" invariant for the
    v0.3 path specifically. Two independently constructed workflows (fresh
    generator, evaluator and policy each time) fed identical contract +
    evidence must yield equal :class:`EvaluationResult` objects — no hidden
    state, no randomness, no LLM. A regression that introduces any
    non-determinism (e.g. a dict-iteration order dependency or a cached
    counter) surfaces as inequality here.
    """
    wf_a, contract, evidence = _green_contract_workflow()
    wf_b, _, _ = _green_contract_workflow()
    criteria = BoundCriteria(threshold=0.6)

    result_a = wf_a.evaluate_step(contract=contract, evidence=evidence, criteria=criteria)
    result_b = wf_b.evaluate_step(contract=contract, evidence=evidence, criteria=criteria)

    assert result_a == result_b
    assert result_a.decision == "ACCEPT"

# ---------------------------------------------------------------------------
# v0.3 Definition of Done: contract workflow reaching ACCEPT without an LLM
# ---------------------------------------------------------------------------


def test_contract_workflow_definition_of_done_reaches_accept_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v0.3 Definition of Done: a contract workflow reaches ACCEPT, no LLM.

    This is the architecture-level gate for the v0.3 Definition of Done as
    written in ``todo.md``::

        natural-language plan -> evaluation contracts -> execution evidence
            -> automatic A / I / R / C -> deterministic BOUND decision

    It is exercised *entirely without an LLM*: a :class:`StaticContractGenerator`
    stands in for the (optional) LLM contract generator, a
    :class:`ContractEvaluator` derives ``A / I / R / C`` from simulated
    :class:`ExecutionEvidence` where every required check passes, and a
    :class:`BoundPolicy` makes the final deterministic decision. Sockets are
    blocked and every provider API-key variable is unset so the gate also
    doubles as the "no network required / no API key required / no mandatory
    LLM dependency" proof. The user supplies *no* manual scores — the four
    dimensions are computed automatically from the contract + evidence.
    """
    _block_sockets(monkeypatch)
    _wipe_api_keys(monkeypatch)

    workflow, contract, evidence = _green_contract_workflow()
    criteria = BoundCriteria(threshold=0.6)

    # Stage 1: natural-language plan -> evaluation contracts (offline generator).
    plan = workflow.prepare(
        goal="Ship the parser",
        plan="Write unit tests and run the linter until both are green.",
    )
    assert plan.steps  # the contract layer defines at least one step
    assert plan.steps[0].acceptance_checks  # ...each with measurable criteria

    # Stage 2: contract + (simulated) execution evidence -> automatic A/I/R/C.
    result = workflow.evaluate_step(contract=contract, evidence=evidence, criteria=criteria)
    scores = result.scores

    # The four dimensions are *derived*, not supplied manually: the user gave
    # no A/I/R/C — only the contract and the evidence.
    assert scores.acceptance == pytest.approx(1.0, abs=1e-12)  # 2/2 required pass
    assert scores.risk == pytest.approx(0.0, abs=1e-12)        # no violated risk checks
    assert scores.cost == pytest.approx(0.0, abs=1e-12)        # no budget declared
    assert scores.influence == pytest.approx(0.0, abs=1e-12)  # not derivable

    # Stage 3: automatic A/I/R/C -> deterministic BOUND decision (ACCEPT).
    assert result.score == pytest.approx(1.0, abs=1e-12)
    assert result.decision == "ACCEPT"
    assert result.threshold == pytest.approx(0.6, abs=1e-12)

    # The decision is reproducible: re-running the identical pipeline yields
    # the same result, locking the "final decision remains deterministic"
    # invariant for the contract path.
    replay = workflow.evaluate_step(contract=contract, evidence=evidence, criteria=criteria)
    assert replay == result

