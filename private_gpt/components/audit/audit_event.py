"""Minimised audit event model.

Data minimisation (mitigation P1.3): the event stores a *hash* of the query, not
the query text, plus the attribution, the requested collection, the authorisation
decision, and whether a cross-collection anomaly was detected. This bounds the
Data-Disclosure and Linking/Identifying exposure that an audit trail creates
(threats T-DFAW-Dd1 / L1 / I1).
"""

from __future__ import annotations

import enum
import hashlib
import hmac
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class AuditDecision(enum.StrEnum):
    """Outcome recorded for an access attempt."""

    ALLOWED = "allowed"
    DENIED = "denied"


class AuditEvent(BaseModel):
    """A single, minimised audit record about a retrieval attempt.

    Intentionally carries no raw query text and no retrieved content — only the
    metadata needed to detect systematic cross-collection probing.
    """

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC time the access attempt was recorded.",
    )
    principal_id: str = Field(
        description="Opaque identifier of the querying principal (already a "
        "pseudonym where the deployment supports it)."
    )
    collection: str = Field(description="Collection the request targeted.")
    query_hash: str = Field(
        description="Salted HMAC-SHA256 of the query text — never the text itself."
    )
    decision: AuditDecision = Field(description="Whether access was allowed or denied.")
    cross_collection_anomaly: bool = Field(
        default=False,
        description="True when the attempt was flagged as probing another "
        "principal's / tenant's collection.",
    )

    @staticmethod
    def hash_query(query: str, *, salt: bytes) -> str:
        """Salted HMAC of the query so equal queries correlate but text is not stored.

        Using an HMAC with a deployment secret (``salt``) prevents trivial
        dictionary reversal of the hash while still allowing the audit consumer
        to spot repeated identical probes.
        """
        return hmac.new(salt, query.encode("utf-8"), hashlib.sha256).hexdigest()
