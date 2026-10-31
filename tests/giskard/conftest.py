"""Fixtures and gating for the Giskard privacy tests.

These tests are OPT-IN and require external resources that are not available in
the default unit-test run:

* the ``giskard`` package with the ``scan`` extra (``pip install 'giskard[scan]'``),
* a judge/embedding LLM for Giskard (``GISKARD_CHECKS_DEFAULT_MODEL`` +
  ``GISKARD_CHECKS_DEFAULT_EMBEDDING_MODEL`` and the provider API key),
* a running PrivateGPT server with the canary corpus ingested
  (``GISKARD_PGPT_BASE_URL`` + the two collections populated).

Every test ``pytest.importorskip("giskard")`` and skips unless
``GISKARD_PGPT_BASE_URL`` is set, so a plain ``make test`` neither imports
Giskard nor contacts a server. See ``doc/giskard-privacy-testing-evaluation.md``
and the runbook in ``doc/giskard-tests-runbook.md``.
"""

from __future__ import annotations

import os

import pytest

from tests.giskard.target import (
    COLLECTION_A,
    COLLECTION_B,
    is_target_configured,
    make_target,
)


def _giskard_enabled() -> bool:
    return os.environ.get("GISKARD_ENABLED", "").lower() in {"1", "true", "yes"}


requires_giskard = pytest.mark.skipif(
    not _giskard_enabled() or not is_target_configured(),
    reason=(
        "Giskard privacy tests are opt-in. Set GISKARD_ENABLED=1 and "
        "GISKARD_PGPT_BASE_URL (plus a judge LLM) to run them. See "
        "doc/giskard-tests-runbook.md."
    ),
)


@pytest.fixture(scope="session")
def giskard():
    """Import giskard or skip the whole module if it is not installed."""
    return pytest.importorskip(
        "giskard",
        reason="Install the extra: pip install 'giskard[scan]' (plus a provider).",
    )


@pytest.fixture
def target_b():
    """Async target scoped to the AUTHORISED collection B (design D1/D2/D3/D4)."""
    return make_target(COLLECTION_B)


@pytest.fixture
def collections() -> dict[str, str]:
    return {"authorised": COLLECTION_B, "foreign": COLLECTION_A}
