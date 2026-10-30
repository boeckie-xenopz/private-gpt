"""Audit recorder and sink abstraction (Option B, separate consumer).

The recorder builds a minimised :class:`AuditEvent` and hands it to an
:class:`AuditSink`. The sink is the seam across the ``TB-AUDIT`` boundary: an
in-memory sink is provided for tests/local use, while a real deployment binds a
sink that forwards to the external audit system (minimised payload only).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from private_gpt.components.audit.audit_event import (  # noqa: TC001
    AuditDecision,
    AuditEvent,
)


@runtime_checkable
class AuditSink(Protocol):
    """Destination for minimised audit events (across TB-AUDIT in Option B)."""

    def emit(self, event: AuditEvent) -> None:
        """Persist/forward a single audit event. Must not raise into the caller."""
        ...


class InMemoryAuditSink:
    """Reference sink that keeps events in memory (tests / local inspection)."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


class AuditRecorder:
    """Builds minimised audit events and emits them to the configured sink.

    The recorder never receives the retrieved content and never stores the raw
    query — it hashes the query with the deployment salt before building the
    event (data minimisation, P1.3).
    """

    def __init__(self, sink: AuditSink, *, salt: bytes) -> None:
        self._sink = sink
        self._salt = salt

    def record(
        self,
        *,
        principal_id: str,
        collection: str,
        query: str,
        decision: AuditDecision,
        cross_collection_anomaly: bool = False,
    ) -> AuditEvent:
        event = AuditEvent(
            principal_id=principal_id,
            collection=collection,
            query_hash=AuditEvent.hash_query(query, salt=self._salt),
            decision=decision,
            cross_collection_anomaly=cross_collection_anomaly,
        )
        self._sink.emit(event)
        return event
