"""Tests for the minimised audit telemetry design (Option B).

Validate the privacy properties that make audit telemetry acceptable under the
scoped LINDDUN analysis (``doc/linddun-elicitation-collection-audit.md`` §8,
risk backlog P1.3 / P2.5):

* **Data minimisation (T-DFAW-Dd1):** the raw query text is NEVER stored; only a
  salted hash is kept.
* **Correlation without content (T-DFAW-L1):** equal queries hash equally (so a
  consumer can spot repeated probes) while different queries differ.
* **Salt dependence:** the hash is not a plain SHA256 of the text, so it cannot
  be trivially reversed with a dictionary.
* **Anomaly flagging:** a denied cross-collection attempt is recorded as an
  anomaly, which is the detection signal the mitigation exists to provide.
"""

import hashlib

from private_gpt.components.audit.audit_event import AuditDecision, AuditEvent
from private_gpt.components.audit.audit_recorder import (
    AuditRecorder,
    InMemoryAuditSink,
)

SALT = b"test-deployment-salt"


def _recorder() -> tuple[AuditRecorder, InMemoryAuditSink]:
    sink = InMemoryAuditSink()
    return AuditRecorder(sink, salt=SALT), sink


def test_raw_query_is_never_stored() -> None:
    """The recorded event must not contain the raw query text anywhere."""
    recorder, sink = _recorder()
    secret_query = "list all employees with salary over 100k named Alice"

    recorder.record(
        principal_id="user-1",
        collection="hr",
        query=secret_query,
        decision=AuditDecision.ALLOWED,
    )

    (event,) = sink.events
    serialised = event.model_dump_json()
    assert secret_query not in serialised
    assert "Alice" not in serialised
    assert event.query_hash != secret_query


def test_equal_queries_hash_equally_for_correlation() -> None:
    """Repeated identical probes produce the same hash (enables detection)."""
    recorder, sink = _recorder()

    recorder.record(
        principal_id="u",
        collection="c",
        query="same probe",
        decision=AuditDecision.DENIED,
    )
    recorder.record(
        principal_id="u",
        collection="c",
        query="same probe",
        decision=AuditDecision.DENIED,
    )

    assert sink.events[0].query_hash == sink.events[1].query_hash


def test_different_queries_hash_differently() -> None:
    h1 = AuditEvent.hash_query("query one", salt=SALT)
    h2 = AuditEvent.hash_query("query two", salt=SALT)
    assert h1 != h2


def test_hash_is_salted_not_plain_sha256() -> None:
    """The hash must depend on the salt (not reversible via a plain digest)."""
    query = "sensitive probe"
    plain = hashlib.sha256(query.encode("utf-8")).hexdigest()
    salted = AuditEvent.hash_query(query, salt=SALT)
    assert salted != plain


def test_hash_depends_on_salt() -> None:
    query = "probe"
    assert AuditEvent.hash_query(query, salt=b"salt-a") != AuditEvent.hash_query(
        query, salt=b"salt-b"
    )


def test_cross_collection_anomaly_is_recorded() -> None:
    """A denied cross-collection attempt is flagged — the detection signal."""
    recorder, sink = _recorder()

    event = recorder.record(
        principal_id="prober",
        collection="other_tenant_collection",
        query="probe",
        decision=AuditDecision.DENIED,
        cross_collection_anomaly=True,
    )

    assert event.decision == AuditDecision.DENIED
    assert event.cross_collection_anomaly is True
    assert sink.events == [event]


def test_minimised_event_fields_only() -> None:
    """The event exposes only the minimised metadata fields (no content/refs text)."""
    recorder, sink = _recorder()
    recorder.record(
        principal_id="u",
        collection="c",
        query="q",
        decision=AuditDecision.ALLOWED,
    )
    (event,) = sink.events
    assert set(event.model_dump().keys()) == {
        "timestamp",
        "principal_id",
        "collection",
        "query_hash",
        "decision",
        "cross_collection_anomaly",
    }
