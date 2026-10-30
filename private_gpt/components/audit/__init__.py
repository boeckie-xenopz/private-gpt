"""Minimised audit telemetry (design proposal, Option B).

Implements the privacy-minimised audit path from the scoped LINDDUN analysis
(``doc/linddun-elicitation-collection-audit.md`` §6/§8.3, risk backlog P1.3,
P2.5): record *who* queried *which collection* and *when*, with the query stored
only as a salted hash — never verbatim — plus a cross-collection anomaly flag.

This is a standalone, minimised recorder intended to feed a *separate* audit
consumer (Option B). It is deliberately NOT the application logger or the
OpenTelemetry tracing pipeline (reusing those would be a purpose-limitation
hazard — scoped §8.4). It is not wired into the retrieval hot path by default;
the application layer decides when/whether to emit.
"""

from private_gpt.components.audit.audit_event import AuditDecision, AuditEvent
from private_gpt.components.audit.audit_recorder import (
    AuditRecorder,
    AuditSink,
    InMemoryAuditSink,
)

__all__ = [
    "AuditDecision",
    "AuditEvent",
    "AuditRecorder",
    "AuditSink",
    "InMemoryAuditSink",
]
