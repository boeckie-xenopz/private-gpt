"""Collection-isolation tests for cross-collection retrieval.

LINDDUN PRO linkage — these tests validate the privacy threats elicited in
``doc/linddun-elicitation-collection-audit.md``:

* **T-DF03-Dd1** — cross-collection disclosure when the tenancy filter is absent
  (physical/``collection`` multitenancy mode, or an empty ``collection``).
* **T-DF03-Dd2** — the tenancy filter is a metadata filter, not an authorisation
  check (it trusts the request-supplied ``collection``).
* **T-DF13-Dd1** — the same filter gates the content read-back path, so the
  isolation property asserted here is what ``/v1/artifacts/content`` relies on.

The isolation boundary is realised entirely by
``VectorStoreComponent.get_filters`` (see
``private_gpt/components/vector_store/vector_store_component.py``). These tests
assert the *privacy property* ("a query scoped to collection B must not be able
to match collection A") directly on that function.

The file has three groups:

* ``*_current_behaviour`` — pin the historical (non-strict) behaviour, which is
  still the default when ``vectorstore.strict_isolation`` is off (scenarios
  S0/S1). These stay green.
* ``*_strict_*`` — assert the fail-closed mitigations P1.1/P1.2 when
  ``strict_isolation`` is enabled (scenario S2): defence-in-depth tenancy
  filtering in every mode, refusal of an empty collection, and authorisation of
  the requested collection.
* authorizer tests — the collection authorisation hook (P1.1).
"""

import pytest
from llama_index.core.vector_stores.types import (
    FilterCondition,
    MetadataFilter,
    MetadataFilters,
)

from private_gpt.components.ingest.metadata_helper import MetadataKeys
from private_gpt.components.vector_store.collection_isolation import (
    CollectionIsolationError,
)
from private_gpt.components.vector_store.vector_store_component import (
    VectorStoreComponent,
)
from tests.fixtures.mock_injector import MockInjector

COLLECTION_KEY = MetadataKeys.COLLECTION.value  # "collection"
ARTIFACT_KEY = MetadataKeys.ARTIFACT_ID.value  # "artifact_id"


def _flatten(filters: MetadataFilters) -> list[MetadataFilter]:
    """Return all leaf MetadataFilter objects in a (possibly nested) tree."""
    leaves: list[MetadataFilter] = []
    for f in filters.filters:
        if isinstance(f, MetadataFilters):
            leaves.extend(_flatten(f))
        else:
            leaves.append(f)
    return leaves


def _collection_filter_values(filters: MetadataFilters) -> list[object]:
    """Values of every leaf filter that constrains the collection key."""
    return [f.value for f in _flatten(filters) if f.key == COLLECTION_KEY]


def _component(
    injector: MockInjector,
    *,
    multitenancy: str,
    strict: bool = False,
) -> VectorStoreComponent:
    injector.bind_settings(
        {
            "vectorstore": {
                "multitenancy": multitenancy,
                "database": "qdrant",
                "strict_isolation": strict,
            }
        }
    )
    return injector.get(VectorStoreComponent)


# ---------------------------------------------------------------------------
# Logical multitenancy — the mode where isolation IS (partially) enforced.
# ---------------------------------------------------------------------------


def test_logical_multitenancy_scopes_to_requested_collection(
    injector: MockInjector,
) -> None:
    """A query for collection B yields a filter constraining COLLECTION == B.

    Property under test (T-DF03-Dd1): in logical multitenancy a non-empty
    collection produces a tenancy filter, so the search cannot match other
    collections' payloads.
    """
    component = _component(injector, multitenancy="logical")

    filters = component.get_filters(collection="collection_b")

    values = _collection_filter_values(filters)
    assert values == ["collection_b"], (
        "logical multitenancy must constrain the query to the requested "
        f"collection; got collection filters {values!r}"
    )


def test_logical_multitenancy_never_targets_other_collection(
    injector: MockInjector,
) -> None:
    """The filter built for B must never reference collection A (T-DF03-Dd1)."""
    component = _component(injector, multitenancy="logical")

    filters = component.get_filters(collection="collection_b")

    assert "collection_a" not in _collection_filter_values(filters)


def test_logical_multitenancy_combines_artifact_filter_under_and(
    injector: MockInjector,
) -> None:
    """Artifact filters are ANDed with the tenancy filter, not ORed.

    If the tenancy filter were combined with OR, an artifact match in another
    collection could leak (T-DF03-Dd2). Assert the top-level condition is AND
    and the collection constraint is present.
    """
    component = _component(injector, multitenancy="logical")

    filters = component.get_filters(
        artifacts=["artifact_in_a"], collection="collection_b"
    )

    assert filters.condition == FilterCondition.AND
    assert "collection_b" in _collection_filter_values(filters)


# ---------------------------------------------------------------------------
# T-DF03-Dd2 — the filter is NOT authorisation: it trusts the request value.
# ---------------------------------------------------------------------------


def test_filter_trusts_request_supplied_collection(injector: MockInjector) -> None:
    """get_filters applies whatever collection it is given — no ownership check.

    This documents T-DF03-Dd2: a caller naming an arbitrary collection gets a
    filter for that collection regardless of entitlement. The authorisation
    decision does not happen here (and, per the threat model, nowhere in the
    current code).
    """
    component = _component(injector, multitenancy="logical")

    # A caller simply asserts someone else's collection name.
    filters = component.get_filters(collection="someone_elses_collection")

    assert _collection_filter_values(filters) == ["someone_elses_collection"], (
        "get_filters trusts the request-supplied collection; this is a metadata "
        "filter, not an authorisation check (T-DF03-Dd2)."
    )


# ---------------------------------------------------------------------------
# T-DF03-Dd1 — fail-OPEN conditions: the tenancy filter is omitted.
# These encode KNOWN GAPS: the privacy-correct behaviour would be fail-closed.
# ---------------------------------------------------------------------------


# Each gap is encoded as a pair:
#   * ``test_*_current_behaviour`` — a plain passing assertion that documents the
#     present (insecure) behaviour, so the suite stays green and the behaviour is
#     pinned (a regression away from it would fail).
#   * ``test_*_fail_closed_desired`` — the privacy-correct behaviour, marked
#     ``xfail(strict=True)``. It fails today (proving the gap is real); when the
#     code is hardened to fail closed it will XPASS and, being strict, force this
#     file to be updated — a built-in reminder that the mitigation landed.


def test_empty_collection_no_tenancy_filter_current_behaviour(
    injector: MockInjector,
) -> None:
    """CURRENT behaviour: an empty collection yields NO tenancy filter.

    ``get_filters`` only adds the tenancy filter ``if self.logical_multitenancy
    and collection`` (vector_store_component.py). With an empty collection the
    guard is false, so the search would run unscoped across every collection
    (T-DF03-Dd1). Pinned here so a regression is caught.
    """
    component = _component(injector, multitenancy="logical")

    filters = component.get_filters(collection="")

    assert _collection_filter_values(filters) == []


def test_empty_collection_fails_closed_under_strict(
    injector: MockInjector,
) -> None:
    """MITIGATION P1.2: strict isolation refuses an empty/missing collection.

    Instead of returning an unscoped filter (the historical gap, T-DF03-Dd1),
    ``get_filters`` now raises ``CollectionIsolationError`` so no unscoped
    retrieval can happen. The API layer maps this to a client error.
    """
    component = _component(injector, multitenancy="logical", strict=True)

    with pytest.raises(CollectionIsolationError):
        component.get_filters(collection="")


def test_physical_multitenancy_no_logical_filter_current_behaviour(
    injector: MockInjector,
) -> None:
    """CURRENT behaviour: physical (``collection``) multitenancy adds no logical filter.

    The ``logical_multitenancy`` guard is false, so ``get_filters`` returns no
    collection constraint; isolation then relies solely on physical collection
    routing in the Qdrant factory (qdrant_factory.py), which this unit test
    cannot observe (T-DF03-Dd1). Pinned as current behaviour.
    """
    component = _component(injector, multitenancy="collection")

    filters = component.get_filters(collection="collection_b")

    assert _collection_filter_values(filters) == []


def test_physical_multitenancy_adds_defence_in_depth_filter_under_strict(
    injector: MockInjector,
) -> None:
    """MITIGATION P1.2: strict isolation adds a tenancy filter even in physical mode.

    Historically (T-DF03-Dd1) physical ``collection`` multitenancy added no
    in-query tenancy filter, relying solely on physical routing. Under strict
    isolation the collection constraint is applied as defence in depth.
    """
    component = _component(injector, multitenancy="collection", strict=True)

    filters = component.get_filters(collection="collection_b")

    assert "collection_b" in _collection_filter_values(filters)


# ---------------------------------------------------------------------------
# P1.1 — collection authorisation hook (fixes T-DF03-Dd2 / T-DF01-Nc1).
# ---------------------------------------------------------------------------


class _AllowListAuthorizer:
    """Test authorizer: only the collections in the allow-list are permitted."""

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def is_authorized(self, collection: str) -> bool:
        return collection in self._allowed


def test_non_strict_mode_does_not_enforce_authorisation(
    injector: MockInjector,
) -> None:
    """Default (non-strict) mode is unchanged: no authorisation is enforced.

    Preserves S0/S1 single-tenant behaviour — the authorizer is not consulted.
    """
    component = _component(injector, multitenancy="logical", strict=False)
    component._authorizer = _AllowListAuthorizer(set())  # authorises nothing

    # Must NOT raise, because strict isolation is off.
    filters = component.get_filters(collection="any_collection")
    assert _collection_filter_values(filters) == ["any_collection"]


def test_strict_mode_allows_authorised_collection(injector: MockInjector) -> None:
    """MITIGATION P1.1: an authorised collection is permitted and scoped."""
    component = _component(injector, multitenancy="logical", strict=True)
    component._authorizer = _AllowListAuthorizer({"collection_b"})

    filters = component.get_filters(collection="collection_b")
    assert _collection_filter_values(filters) == ["collection_b"]


def test_strict_mode_denies_unauthorised_collection(injector: MockInjector) -> None:
    """MITIGATION P1.1: naming another tenant's collection is refused (T-DF03-Dd2).

    The request-supplied collection is no longer trusted blindly; the authorizer
    denies access and ``get_filters`` raises instead of building a filter that
    would read someone else's collection.
    """
    component = _component(injector, multitenancy="logical", strict=True)
    component._authorizer = _AllowListAuthorizer({"my_collection"})

    with pytest.raises(CollectionIsolationError):
        component.get_filters(collection="someone_elses_collection")
