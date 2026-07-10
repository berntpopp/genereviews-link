"""Security contract for strict Host and Origin validation."""

from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient
from packaging.version import Version
from pydantic import ValidationError

from genereview_link.config import ServerConfig, Settings, settings
from genereview_link.server_manager import UnifiedServerManager


@pytest.fixture
def guarded_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        settings,
        "MCP_ALLOWED_HOSTS",
        ["localhost", "127.0.0.1", "::1", "genereviews-link.example.org"],
    )
    monkeypatch.setattr(
        settings,
        "MCP_ALLOWED_ORIGINS",
        ["https://genereviews-link.example.org"],
    )
    app = UnifiedServerManager().create_fastapi_app(ServerConfig(enable_docs=True))
    return TestClient(app)


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]", "[::1]:8000"],
)
def test_loopback_hosts_are_allowed(guarded_client: TestClient, host: str) -> None:
    assert guarded_client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["genereviews-link.example.org", "genereviews-link.example.org:8443"]
)
def test_configured_public_host_is_allowed(guarded_client: TestClient, host: str) -> None:
    assert guarded_client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/health", "/docs"])
def test_unlisted_host_is_rejected_on_every_route(guarded_client: TestClient, path: str) -> None:
    response = guarded_client.get(path, headers={"Host": "attacker.example"})
    assert response.status_code == 421


@pytest.mark.parametrize("path", ["/", "/health", "/docs"])
def test_unlisted_origin_is_rejected_on_every_route(guarded_client: TestClient, path: str) -> None:
    response = guarded_client.get(
        path,
        headers={"Host": "localhost", "Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("origin", [None, "https://genereviews-link.example.org"])
def test_absent_or_configured_origin_is_allowed(
    guarded_client: TestClient, origin: str | None
) -> None:
    headers = {"Host": "localhost"}
    if origin is not None:
        headers["Origin"] = origin
    assert guarded_client.get("/", headers=headers).status_code == 200


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MCP_ALLOWED_HOSTS", ["*.example.org"]),
        ("MCP_ALLOWED_HOSTS", ["host?.example.org"]),
        ("MCP_ALLOWED_ORIGINS", ["https://*.example.org"]),
    ],
)
def test_wildcard_allowlist_entries_are_rejected(field: str, value: list[str]) -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(**{field: value})


def test_allowlists_load_from_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", '["api.example.org"]')
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", '["https://app.example.org"]')
    configured = Settings(_env_file=None)
    assert configured.MCP_ALLOWED_HOSTS == ["api.example.org"]
    assert configured.MCP_ALLOWED_ORIGINS == ["https://app.example.org"]


def test_fastmcp_supports_native_strict_host_origin_configuration() -> None:
    assert Version(version("fastmcp")) >= Version("3.4.4")
