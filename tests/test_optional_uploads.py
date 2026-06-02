"""End-to-end (TestClient) coverage for optional uploads
(`required: false`, 2.1.0): the field can be omitted over HTTP
without a 400, present when supplied, and a still-required upload
is still enforced. Echoes the rendered paths via /bin/echo."""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code.config import AppConfig
from url2code.main import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(AppConfig.model_validate({
        "api": {"title": "t", "default_root": "/v1"},
        "endpoints": [{
            "name": "cite",
            "route": "/cite",
            "method": "POST",
            "command": {"executable": "/bin/echo", "args": ["doc={doc}", "bib={bib}"]},
            "uploads": [
                {"field_name": "document", "placeholder": "doc",
                 "temp_dir": str(tmp_path / "u")},
                {"field_name": "bibliography", "placeholder": "bib",
                 "temp_dir": str(tmp_path / "u"), "required": False},
            ],
        }],
    })))


def test_optional_upload_can_be_omitted(tmp_path) -> None:
    client = _client(tmp_path)
    r = client.post("/v1/cite", files={"document": ("d.md", b"hi", "text/plain")})
    assert r.status_code == 200, r.text
    out = r.json()["stdout"]
    assert "doc=" in out
    # The omitted optional upload renders as an empty placeholder.
    assert out.rstrip().endswith("bib=")


def test_optional_upload_present_is_passed(tmp_path) -> None:
    client = _client(tmp_path)
    r = client.post("/v1/cite", files={
        "document": ("d.md", b"hi", "text/plain"),
        "bibliography": ("refs.bib", b"@book{x,title={Y}}", "text/plain"),
    })
    assert r.status_code == 200, r.text
    assert ".bib" in r.json()["stdout"]


def test_required_upload_still_enforced(tmp_path) -> None:
    client = _client(tmp_path)
    # document (required) omitted -> 400, even though the optional
    # bibliography is present.
    r = client.post("/v1/cite", files={"bibliography": ("refs.bib", b"@book{x}", "text/plain")})
    assert r.status_code == 400
