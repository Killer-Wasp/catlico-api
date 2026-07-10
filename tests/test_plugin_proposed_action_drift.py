"""Drift guard pinning the plugin runtime's proposal-creating routes to
``_APPLICABLE``.

``apply()`` can only execute action types in ``_APPLICABLE``. If a
``plugin_runtime.py`` route proposes an ``action_type`` that isn't applicable,
every such proposal 422s on approval and no behavioural test catches it. This
guard statically extracts the action types the runtime actually produces and
asserts they are a subset of ``_APPLICABLE`` (the safe direction — a producing
route for a non-applicable type is the bug).

Mirrors the AST-based drift guards in
``catlico-plugin-sdk/tests/test_cli.py``: parse the sibling module with ``ast``
rather than relying on runtime wiring.
"""
import ast
from pathlib import Path

from app.api.internal.routes import plugin_runtime
from app.crud.plugin_proposed_action import _APPLICABLE


def _proposed_action_types(source: str) -> set[str]:
    """Every ``action_type`` value passed to a ``ppa_crud.create(...)`` call.

    Fails loudly if such a value isn't a static string literal, so a refactor to a
    computed/variable ``action_type`` can't silently slip past this static guard.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "ppa_crud"):
            continue
        for kw in node.keywords:
            if kw.arg != "action_type":
                continue
            assert isinstance(kw.value, ast.Constant) and isinstance(
                kw.value.value, str
            ), (
                "plugin_runtime.py passes a non-literal action_type to "
                "ppa_crud.create; the static drift guard can no longer verify it"
            )
            found.add(kw.value.value)
    return found


def test_runtime_proposed_action_types_are_applicable():
    source = Path(plugin_runtime.__file__).read_text()
    produced = _proposed_action_types(source)

    # Vacuity guard: a rename that breaks extraction (e.g. ppa_crud.create is
    # renamed or aliased) must fail loudly, never silently pass with an empty set.
    assert produced, (
        "found no action_type literals passed to ppa_crud.create in "
        "plugin_runtime.py — the extraction broke and would otherwise pass "
        "vacuously"
    )

    not_applicable = produced - _APPLICABLE
    assert not not_applicable, (
        f"plugin_runtime.py proposes action_type(s) {sorted(not_applicable)} that "
        f"apply() cannot execute (_APPLICABLE={sorted(_APPLICABLE)}); every such "
        "proposal 422s on approval. Add them to _APPLICABLE or stop proposing them."
    )


def test_drift_guard_detects_a_non_applicable_producer():
    """The guard must bite: a producing route emitting an unlisted action_type
    fails the subset assertion. Simulate it against synthesized source so no file
    is modified."""
    drifted = (
        "action = await ppa_crud.create(\n"
        "    session, run=run, action_type='delete_the_universe',\n"
        "    entity_type='case', entity_id='1', payload={},\n"
        ")\n"
    )
    produced = _proposed_action_types(drifted)
    assert "delete_the_universe" in produced
    assert produced - _APPLICABLE == {"delete_the_universe"}


def test_drift_guard_is_not_vacuous_on_no_producers():
    """If extraction finds nothing (a rename broke it), the real test's vacuity
    guard fires. Prove the extractor returns empty for source with no matching
    call, so that guard is meaningful."""
    assert _proposed_action_types("x = 1\n") == set()
