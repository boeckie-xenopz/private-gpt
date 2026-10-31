"""Ingest the two-collection canary corpus into a running PrivateGPT.

Setup helper for the Giskard privacy tests (design D1/D2). Ingests collection A
(containing CANARY_ALPHA) and collection B (containing CANARY_BETA) via the
public ``POST /v1/artifacts/ingest`` endpoint, so the cross-collection tests have
data to leak (or correctly isolate).

Usage:
    GISKARD_PGPT_BASE_URL=http://localhost:8080 \
    GISKARD_PGPT_AUTH="Basic ..."   # if auth enabled \
    python -m tests.giskard.ingest_canary_corpus

Run this once against the deployment-under-test before running the Giskard tests.
This is a test-setup utility, not product code.
"""

from __future__ import annotations

import base64
import os
import sys

import httpx

from tests.giskard.target import (
    COLLECTION_A,
    COLLECTION_B,
    CORPUS_A,
    CORPUS_B,
)


def _ingest_one(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    collection: str,
    artifact: str,
    text: str,
) -> None:
    value = base64.b64encode(text.encode("utf-8")).decode("ascii")
    body = {
        "artifact": artifact,
        "collection": collection,
        "input": {"type": "text", "value": value},
        "metadata": {"file_name": f"{artifact}.txt"},
    }
    resp = client.post(f"{base_url}/v1/artifacts/ingest", json=body, headers=headers)
    resp.raise_for_status()
    print(f"  ingested {collection}/{artifact}")


def main() -> int:
    base_url = os.environ.get("GISKARD_PGPT_BASE_URL")
    if not base_url:
        print("GISKARD_PGPT_BASE_URL is not set.", file=sys.stderr)
        return 2
    base_url = base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    auth = os.environ.get("GISKARD_PGPT_AUTH")
    if auth:
        headers["Authorization"] = auth

    timeout = float(os.environ.get("GISKARD_PGPT_TIMEOUT", "120"))
    with httpx.Client(timeout=timeout) as client:
        print(f"Ingesting canary corpus into {base_url} ...")
        for artifact, text in CORPUS_A:
            _ingest_one(client, base_url, headers, COLLECTION_A, artifact, text)
        for artifact, text in CORPUS_B:
            _ingest_one(client, base_url, headers, COLLECTION_B, artifact, text)
    print("Done. Collections A and B are populated with their canaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
