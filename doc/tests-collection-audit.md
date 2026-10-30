# Tests & Mitigations — Cross-Collection Isolation and Audit Telemetry

**Document type:** Test documentation + mitigation implementation notes
**Companion documents:**
[`linddun-elicitation-collection-audit.md`](./linddun-elicitation-collection-audit.md) (threats) ·
[`risk-prioritisation-collection-audit.md`](./risk-prioritisation-collection-audit.md) (backlog) ·
[`scoped-analysis-collection-audit.md`](./scoped-analysis-collection-audit.md) (scope)

This document describes the executable tests and the code changes that back the
scoped LINDDUN analysis: the collection-isolation tests, the fail-closed
isolation mitigations (**P1.1 / P1.2**), and the minimised audit-telemetry
design (**Option B**, backlog P1.3 / P2.5).

---

## Table of contents

1. [What is covered and where it lives](#1-what-is-covered-and-where-it-lives)
2. [Environment and build instructions](#2-environment-and-build-instructions)
3. [Collection-isolation tests](#3-collection-isolation-tests)
4. [Mitigations P1.1 / P1.2 — fail-closed isolation](#4-mitigations-p11--p12--fail-closed-isolation)
5. [Audit telemetry (Option B, minimised) and its tests](#5-audit-telemetry-option-b-minimised-and-its-tests)
6. [Threat → test traceability](#6-threat--test-traceability)
7. [Running the tests](#7-running-the-tests)
8. [Notes and limitations](#8-notes-and-limitations)

---

## 1. What is covered and where it lives

| Area | Source under test | Test file |
|---|---|---|
| Collection isolation (retrieval filter) | `private_gpt/components/vector_store/vector_store_component.py` (`get_filters`) | `tests/components/vector_store/test_collection_isolation.py` |
| Fail-closed isolation + authorisation (P1.1/P1.2) | `private_gpt/components/vector_store/collection_isolation.py` | same file (strict-mode + authorizer tests) |
| Minimised audit telemetry (Option B) | `private_gpt/components/audit/` | `tests/components/audit/test_audit_recorder.py` |

New source modules added for the mitigations:

- `private_gpt/components/vector_store/collection_isolation.py` — `CollectionIsolationError`, `CollectionAuthorizer` protocol, `AllowAllCollectionAuthorizer`, `ensure_collection_access`.
- `private_gpt/components/audit/audit_event.py` — `AuditEvent`, `AuditDecision`, salted `hash_query`.
- `private_gpt/components/audit/audit_recorder.py` — `AuditRecorder`, `AuditSink` protocol, `InMemoryAuditSink`.
- Settings: `VectorstoreSettings.strict_isolation` in `private_gpt/settings/settings.py`.

---

## 2. Environment and build instructions

The project uses **Python 3.11** and **[uv](https://docs.astral.sh/uv/)**. The
canonical commands come from the `Makefile`:

```bash
# Install the project + dev dependencies (editable)
uv sync --extra core --extra vectorstore-qdrant --extra queue --group test

# Run the whole suite the way CI does
make test

# Run just the scoped tests
make test PYTEST_ARGS="tests/components/vector_store/test_collection_isolation.py \
                       tests/components/audit/test_audit_recorder.py"
```

Under the hood `make test` runs (see `Makefile`):

```bash
PGPT_HOME=$(pwd) PYTHONPATH=. uv run pytest tests -v
```

Tests load the **test profile** `settings-test.yaml` (vector store = embedded
Qdrant on disk, mock LLM/embedding, `multitenancy: logical`, workers disabled).
The scoped tests do not need a live model: they exercise `get_filters` and the
audit recorder directly and override settings per test via the `injector`
fixture (`tests/fixtures/mock_injector.py`), so they are fast and hermetic.

> **Filesystem caveat (observed in this workspace).** If the repository lives on
> a filesystem that does not support symlinks (e.g. certain network/overlay
> mounts), `uv`/`venv` cannot create the in-repo `.venv` (symlink error 95). In
> that case create the virtualenv **outside** the repo and point tooling at it:
>
> ```bash
> python3 -m venv --copies ~/.venv-pgpt   # fails in-repo, works under $HOME
> VIRTUAL_ENV=~/.venv-pgpt UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=~/.venv-pgpt \
>   uv sync --active --extra core --extra vectorstore-qdrant --extra queue
> VIRTUAL_ENV=~/.venv-pgpt UV_LINK_MODE=copy uv pip install \
>   --python ~/.venv-pgpt/bin/python pytest pytest-asyncio pytest-xdist pytest-httpx ruff
> PGPT_PROFILES=test PGPT_HOME=$(pwd) PYTHONPATH=. \
>   ~/.venv-pgpt/bin/python -m pytest tests/components/vector_store/test_collection_isolation.py -v
> ```
>
> The `queue` extra is required because the shared test fixtures import the full
> FastAPI app, whose import chain pulls in Celery.

Lint:

```bash
uv run ruff check private_gpt tests
```

---

## 3. Collection-isolation tests

File: `tests/components/vector_store/test_collection_isolation.py`.

These assert the privacy property directly on `VectorStoreComponent.get_filters`
— *"a query scoped to collection B must not be able to match collection A"* —
which is the single mechanism isolating collections at retrieval and content
read-back (threats **T-DF03-Dd1**, **T-DF03-Dd2**, **T-DF13-Dd1**).

| Test | Asserts | Threat |
|---|---|---|
| `test_logical_multitenancy_scopes_to_requested_collection` | logical mode adds a `COLLECTION == B` filter | T-DF03-Dd1 (positive) |
| `test_logical_multitenancy_never_targets_other_collection` | the B-filter never references A | T-DF03-Dd1 |
| `test_logical_multitenancy_combines_artifact_filter_under_and` | artifact filters are ANDed with tenancy, not ORed | T-DF03-Dd2 |
| `test_filter_trusts_request_supplied_collection` | non-strict mode trusts the request `collection` (documents the gap) | T-DF03-Dd2 |
| `test_empty_collection_no_tenancy_filter_current_behaviour` | non-strict: empty collection → no filter (pins current behaviour) | T-DF03-Dd1 |
| `test_physical_multitenancy_no_logical_filter_current_behaviour` | non-strict physical mode → no logical filter (pins current behaviour) | T-DF03-Dd1 |

The two `*_current_behaviour` tests deliberately pin the historical (non-strict)
behaviour so a regression *away* from it is caught, while the mitigation is
proven by the strict-mode tests below.

---

## 4. Mitigations P1.1 / P1.2 — fail-closed isolation

**Implementation.** `get_filters` now consults
`ensure_collection_access(...)` and applies the tenancy filter in a fail-closed
way, gated by a new opt-in setting:

- `VectorstoreSettings.strict_isolation` (default **False**) — keeps
  single-tenant S0/S1 behaviour unchanged; enable for multi-tenant S2.
- **P1.2 fail-closed filtering:** when strict, the collection tenancy filter is
  applied in *every* multitenancy mode (defence in depth), and a missing/empty
  collection raises `CollectionIsolationError` instead of running unscoped.
- **P1.1 authorisation:** a pluggable `CollectionAuthorizer` is consulted before
  retrieval. The default binding is `AllowAllCollectionAuthorizer` (explicit
  "no enforcement", preserving current behaviour); a multi-tenant deployment
  binds an implementation mapping the current `Principal` to entitled
  collections. An unauthorised collection raises `CollectionIsolationError`.

**Tests proving the mitigation** (all passing):

| Test | Asserts | Backlog item |
|---|---|---|
| `test_empty_collection_fails_closed_under_strict` | strict + empty collection → raises `CollectionIsolationError` | P1.2 |
| `test_physical_multitenancy_adds_defence_in_depth_filter_under_strict` | strict + physical mode → tenancy filter still applied | P1.2 |
| `test_non_strict_mode_does_not_enforce_authorisation` | default mode unchanged (authorizer not consulted) | P1.1 (compat) |
| `test_strict_mode_allows_authorised_collection` | strict + authorised collection → permitted and scoped | P1.1 |
| `test_strict_mode_denies_unauthorised_collection` | strict + unauthorised collection → raises | P1.1 (fixes T-DF03-Dd2) |

**Non-breaking verification.** Because `strict_isolation` defaults to False, the
existing retrieval/ingest suites are unaffected — confirmed by running
`tests/components/workflows` and `tests/components/ingest` (41 passed) alongside
the vector-store suite (23 passed).

---

## 5. Audit telemetry (Option B, minimised) and its tests

**Design.** `private_gpt/components/audit/` implements the *minimised, separate*
audit path recommended in the scoped analysis (§6/§8.3). It is deliberately
**not** the application logger and **not** the OpenTelemetry tracing pipeline —
reusing those would be a purpose-limitation hazard (scoped §8.4). It is a
standalone recorder intended to feed a separate consumer across the `TB-AUDIT`
boundary, and is **not wired into the retrieval hot path by default** (the
application layer decides when to emit).

Key privacy properties built into the design:

- **Data minimisation (P1.3):** `AuditEvent` stores a **salted HMAC-SHA256 hash**
  of the query (`AuditEvent.hash_query`), never the query text, plus
  attribution, collection, allow/deny decision, and a cross-collection anomaly
  flag. No retrieved content is recorded.
- **Correlation without content:** equal queries hash equally (so a consumer can
  detect repeated probes) but the text is not recoverable from the hash.
- **Sink seam (`AuditSink`):** the `TB-AUDIT` boundary; `InMemoryAuditSink` is
  the reference/test sink, a deployment binds a forwarding sink.

**Tests** (file `tests/components/audit/test_audit_recorder.py`, all passing):

| Test | Asserts | Threat addressed |
|---|---|---|
| `test_raw_query_is_never_stored` | the event JSON contains neither the query nor identifying substrings | T-DFAW-Dd1 |
| `test_equal_queries_hash_equally_for_correlation` | identical probes correlate via equal hashes | enables detection (mitigation value) |
| `test_different_queries_hash_differently` | distinct queries → distinct hashes | — |
| `test_hash_is_salted_not_plain_sha256` | hash ≠ plain SHA256 (not dictionary-reversible) | T-DFAW-Dd1 |
| `test_hash_depends_on_salt` | different salts → different hashes | T-DFAW-Dd1 |
| `test_cross_collection_anomaly_is_recorded` | denied cross-collection attempt flagged | detection signal for issue 1 |
| `test_minimised_event_fields_only` | event exposes only the minimised fields | T-DFAW-Dd1 / minimisation |

---

## 6. Threat → test traceability

| Threat (elicitation) | Risk band | Test(s) | Status |
|---|---|---|---|
| T-DF03-Dd1 (cross-collection, filter absent) | Critical | `*_current_behaviour` (gap) + `*_under_strict` (fix) | gap pinned; fix proven |
| T-DF03-Dd2 (filter ≠ authorisation) | Critical | `test_filter_trusts_request_supplied_collection` + `test_strict_mode_denies_unauthorised_collection` | gap pinned; fix proven |
| T-DF13-Dd1 (whole-document cross-collection read) | High | gated by the same `get_filters` → covered transitively | covered |
| T-DFAW-Dd1 (audit stores sensitive behaviour) | High | audit minimisation tests (§5) | mitigated by design |
| T-DFAW-L1 (behavioural profiling) | Critical | `test_equal_queries_hash_equally…` (correlation without content) | bounded |

---

## 7. Running the tests

```bash
# All scoped tests
make test PYTEST_ARGS="tests/components/vector_store/test_collection_isolation.py \
                       tests/components/audit/test_audit_recorder.py"
```

Expected result at time of writing:

```
tests/components/vector_store/test_collection_isolation.py ... 11 passed
tests/components/audit/test_audit_recorder.py ............... 7 passed
```

Regression check that the mitigation is non-breaking:

```bash
make test PYTEST_ARGS="tests/components/vector_store tests/components/workflows tests/components/ingest"
# 23 + 41 passed (vector_store incl. isolation; workflows; ingest)
```

---

## 8. Notes and limitations

- **Strict isolation is opt-in.** `strict_isolation` defaults to False to avoid
  changing single-tenant (S0/S1) behaviour. Multi-tenant (S2) deployments must
  enable it *and* bind a real `CollectionAuthorizer`; the default
  `AllowAllCollectionAuthorizer` enforces nothing by design.
- **Physical-mode routing not covered end-to-end.** The strict-mode test proves
  a defence-in-depth *in-query* filter is added in physical multitenancy; it
  does not exercise live physical Qdrant routing (out of unit-test scope;
  flagged in the risk doc §8).
- **Audit recorder is a design proposal.** It is implemented and unit-tested but
  intentionally not wired into retrieval; wiring it (and binding a forwarding
  sink + deployment salt) is a deployment decision with the Option A/B
  trade-offs documented in the scoped analysis.
- **Salt management is out of scope here.** `hash_query` takes the salt as a
  parameter; secure provisioning/rotation of the deployment salt is a follow-up.

---

*These tests and mitigations operationalise the scoped LINDDUN analysis: the
isolation tests pin the cross-collection gaps, P1.1/P1.2 close them behind an
opt-in strict mode, and the minimised audit module provides a privacy-bounded
Option-B telemetry path — each with passing, source-referenced tests.*
