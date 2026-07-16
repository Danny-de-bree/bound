"""Architecture test: no agent-framework dependency in the BOUND core (Phase 16).

Phase 16 explicitly requires a test that the deterministic core has **no
framework dependency** — i.e. it must not pull in (or import) any agent /
orchestration framework (LangGraph, AutoGen, CrewAI, LlamaIndex, smolagents,
…) any more than it may pull an LLM provider SDK. BOUND is *framework-neutral*:
an agent wires itself in by reading ``bound integration-spec`` and calling the
thin ``evaluate_agent_step`` helper — BOUND never imports the agent's framework.

The companion ``tests/test_architecture.py`` guards the LLM-provider / network
invariant. This file adds the explicit, positively-named framework invariant:

1. **Dependency metadata** — the installed ``bound`` distribution's *runtime*
   requirements must be a subset of the allow-list ``{pydantic}`` (a data
   modelling library is not a framework). A *positive* check: not just "no
   forbidden package present" but "the only thing we need at runtime is the
   data-modelling layer".
2. **Static import scan** — no ``.py`` file under ``src/bound`` may import any
   known agent-framework module root (AST-parsed so docstrings/comments cannot
   false-positive).
3. **Runtime** — importing ``bound`` loads no agent framework into
   ``sys.modules``.
"""

from __future__ import annotations

import ast
import importlib.metadata
import sys
from pathlib import Path

import pytest

#: Runtime dependencies the BOUND core is permitted to declare. Pydantic is the
#: data-modelling/validation layer — it is not an agent framework, an LLM SDK, or
#: a networking library. If this set grows it must stay free of any agent /
#: orchestration framework or provider SDK.
_ALLOWED_RUNTIME_REQUIREMENTS = frozenset({"pydantic"})

#: Known agent / orchestration frameworks. BOUND must never import any of these
#: (nor declare them as a runtime dependency). Module roots are used because an
#: ``import`` statement targets a module name, which may differ from the PyPI
#: distribution name (e.g. ``llama-index`` -> ``llama_index``).
_AGENT_FRAMEWORK_IMPORT_ROOTS = frozenset(
    {
        "langchain",
        "langgraph",
        "langgraph_sdk",
        "autogen",
        "autogen_agentchat",
        "crewai",
        "llama_index",
        "llama_index_core",
        "smolagents",
        "agno",
        "haystack",
        "dspy",
        "semantic_kernel",
        "magentic",
        "beeagent_framework",
        "mastra",
    }
)

#: The installed ``bound`` package source tree.
_SRC_ROOT = Path(__import__("bound").__file__).resolve().parent


def _canonical_requirement_name(requirement: str) -> str:
    """Extract the canonical, lowercased package name from a requirement string.

    PEP 508 requirement strings carry version specifiers, extras and markers
    (e.g. ``"pydantic>=2.0"`` or ``"foo[bar]; python_version<'3.10'"``). We only
    need the bare distribution name, so everything from the first
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

    Walks the parsed AST and collects the root segment of every ``import`` and
    ``from ... import`` target. AST parsing (rather than a text grep) means names
    appearing only in docstrings or comments do not produce false positives.

    Args:
        source_path: A ``.py`` file to scan.

    Returns:
        The set of top-level imported module roots (e.g. ``{"pydantic"}``).
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_runtime_dependencies_are_framework_free() -> None:
    """The installed ``bound`` distribution declares only the allow-listed runtime deps.

    A positive check: not merely "no forbidden framework is present" but "the
    only thing the core needs at runtime is the data-modelling layer". If a
    contributor adds an agent framework (or any non-pydantic runtime dep) to
    ``[project.dependencies]``, this fails loudly before release.
    """
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
    extra = declared - _ALLOWED_RUNTIME_REQUIREMENTS
    assert not extra, (
        "BOUND runtime dependencies must stay framework-free (only pydantic is "
        f"permitted); unexpected runtime requirements: {sorted(extra)}"
    )


def test_bound_source_imports_no_agent_framework() -> None:
    """No ``src/bound`` module imports any known agent / orchestration framework.

    The framework-neutral invariant at the source level: BOUND must never
    ``import`` an agent framework, so an integration can never be coupled to a
    specific framework by BOUND itself. AST-parsed to avoid false positives from
    docstrings/comments.
    """
    offenders: dict[str, set[str]] = {}
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        forbidden = _module_roots_imported_by(path) & _AGENT_FRAMEWORK_IMPORT_ROOTS
        if forbidden:
            offenders[str(path.relative_to(_SRC_ROOT))] = forbidden

    assert not offenders, (
        "BOUND core must not import any agent/orchestration framework; "
        f"found: {offenders}"
    )


@pytest.mark.parametrize("module_root", sorted(_AGENT_FRAMEWORK_IMPORT_ROOTS))
def test_agent_framework_not_loaded_after_import(module_root: str) -> None:
    """Importing ``bound`` does not load any agent framework into ``sys.modules``.

    A runtime complement: even if a framework were an optional, undeclared
    dependency, importing the public package must not pull it in. Each framework
    root in the deny-list must be absent from ``sys.modules`` after ``import
    bound``.
    """
    import bound  # noqa: F401  (import side-effect under test)

    loaded_roots = {name.split(".")[0] for name in sys.modules}
    assert module_root not in loaded_roots, (
        f"importing bound loaded agent framework module '{module_root}'"
    )

