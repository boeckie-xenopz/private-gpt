"""Collection isolation: fail-closed filtering and an authorisation hook.

Mitigations for the scoped LINDDUN analysis
(``doc/linddun-elicitation-collection-audit.md`` / risk backlog P1.1, P1.2):

* **P1.2 — fail-closed tenancy filtering.** A tenancy filter is applied for the
  requested collection in *every* multitenancy mode (defence in depth), and a
  missing/empty collection is refused rather than running an unscoped query.
* **P1.1 — collection authorisation.** A pluggable
  :class:`CollectionAuthorizer` decides whether the current principal may use a
  collection *before* retrieval, instead of trusting the request-supplied
  ``collection`` string (threat T-DF03-Dd2 / T-DF01-Nc1).

Both are opt-in via ``vectorstore.strict_isolation`` so that single-tenant
deployments (scenarios S0/S1) keep their current behaviour, while multi-tenant
deployments (S2) can enforce isolation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class CollectionIsolationError(Exception):
    """Raised when collection isolation is violated or cannot be guaranteed.

    Carrying a dedicated type lets the API layer translate it to a 403/400
    rather than leaking an unscoped query or a 500.
    """


@runtime_checkable
class CollectionAuthorizer(Protocol):
    """Decides whether a principal may access a collection.

    Implementations live in the application layer (they need the deployment's
    identity model). The default binding is :class:`AllowAllCollectionAuthorizer`,
    which preserves the historical behaviour; a multi-tenant deployment binds a
    real implementation that maps the current ``Principal`` to its entitled
    collections.
    """

    def is_authorized(self, collection: str) -> bool:
        """Return True if the current principal may use *collection*."""
        ...


class AllowAllCollectionAuthorizer:
    """Default authorizer: allows every collection (no enforcement).

    This keeps the out-of-the-box, single-tenant behaviour unchanged. It is the
    explicit, named "no authorisation" implementation so that the absence of
    enforcement is a deliberate, visible binding rather than silent.
    """

    def is_authorized(self, collection: str) -> bool:
        _ = collection  # allow-all: every collection is authorised
        return True


def ensure_collection_access(
    authorizer: CollectionAuthorizer,
    collection: str | None,
    *,
    strict: bool,
) -> None:
    """Fail closed before retrieval when strict isolation is enabled.

    * A missing/empty collection is refused (no unscoped retrieval).
    * An unauthorised collection is refused.

    When ``strict`` is False this is a no-op, preserving S0/S1 behaviour.
    """
    if not strict:
        return
    if not collection:
        raise CollectionIsolationError(
            "A collection must be specified; unscoped retrieval is refused "
            "under strict collection isolation."
        )
    if not authorizer.is_authorized(collection):
        raise CollectionIsolationError(
            f"Access to collection {collection!r} is not authorised for the "
            "current principal."
        )
