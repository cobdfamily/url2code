"""Tests for the readiness/liveness split (1.4.0).

Unit-level: resolve_executable / missing_executables in config.py.
Route-level: GET /readyz returns 200 when every wrapped CLI is
present and 503 (with the missing names) when one isn't, while
GET / liveness stays 200 regardless."""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code.config import AppConfig, missing_executables, resolve_executable
from url2code.main import create_app


def _cfg(executables: list[str]) -> AppConfig:
    return AppConfig.model_validate({
        "endpoints": [
            {
                "name": f"e{i}",
                "route": f"/e{i}",
                "method": "GET",
                "command": {"executable": ex},
            }
            for i, ex in enumerate(executables)
        ],
    })


def test_resolve_absolute_path_present() -> None:
    assert resolve_executable("/bin/sh") is True


def test_resolve_absolute_path_absent() -> None:
    assert resolve_executable("/nonexistent/definitely/not/here") is False


def test_resolve_bare_name_on_path() -> None:
    assert resolve_executable("sh") is True


def test_resolve_bare_name_missing() -> None:
    assert resolve_executable("url2code-no-such-binary-xyz") is False


def test_missing_executables_empty_when_all_present() -> None:
    assert missing_executables(_cfg(["/bin/sh", "sh"])) == []


def test_missing_executables_dedupes_and_sorts() -> None:
    cfg = _cfg(["/bin/sh", "zzz-missing-xyz", "aaa-missing-xyz", "zzz-missing-xyz"])
    assert missing_executables(cfg) == ["aaa-missing-xyz", "zzz-missing-xyz"]


def test_readyz_200_when_executables_present() -> None:
    client = TestClient(create_app(_cfg(["/bin/echo"])))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checked": 1}


def test_readyz_503_lists_missing_executables() -> None:
    client = TestClient(create_app(_cfg(["/bin/echo", "totally-missing-binary-xyz"])))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "not ready",
        "missing": ["totally-missing-binary-xyz"],
    }


def test_liveness_root_stays_ok_regardless() -> None:
    # Even with a missing executable (not ready), the process is
    # alive, so / must still be 200.
    client = TestClient(create_app(_cfg(["totally-missing-binary-xyz"])))
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
