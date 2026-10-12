from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def propose_ok(client: TestClient, url: str) -> dict[str, Any]:
    response = client.post(url)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body
