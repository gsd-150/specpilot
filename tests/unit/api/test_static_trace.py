from __future__ import annotations

import importlib
import re
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from specpilot.api.app import create_app
from specpilot.api.static import PackageTraceAssets, install_trace_routes

pytestmark = pytest.mark.anyio

SOURCE = "a" * 64
CORPUS = "b" * 64


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _asset_urls(html: str) -> list[str]:
    return re.findall(r'(?:src|href)="(/trace/assets/[^"]+)"', html)


async def test_trace_index_injects_only_exact_manifest_hashes() -> None:
    runtime = SimpleNamespace(
        binding=SimpleNamespace(
            profile="real",
            source_manifest_id=SOURCE,
            corpus_manifest_id=CORPUS,
        ),
        bind_host="127.0.0.1",
        demo_issuer=None,
    )
    app = create_app(runtime=runtime)  # type: ignore[arg-type]
    async with await _client(app) as client:
        response = await client.get("/trace")

    assert response.status_code == 200
    assert f'data-source-manifest-id="{SOURCE}"' in response.text
    assert f'data-corpus-manifest-id="{CORPUS}"' in response.text
    assert "provider" not in response.text.lower()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]


async def test_unconfigured_app_serves_fail_closed_trace_template() -> None:
    async with await _client(create_app()) as client:
        response = await client.get("/trace")

    assert response.status_code == 200
    assert 'data-source-manifest-id=""' in response.text
    assert 'data-corpus-manifest-id=""' in response.text


async def test_invalid_binding_values_are_never_reflected() -> None:
    app = FastAPI()
    install_trace_routes(
        app,
        source_manifest_id='"><script>alert(1)</script>',
        corpus_manifest_id="not-a-hash",
    )
    async with await _client(app) as client:
        response = await client.get("/trace")

    assert response.status_code == 200
    assert 'data-source-manifest-id=""' in response.text
    assert 'data-corpus-manifest-id=""' in response.text
    assert "alert" not in response.text


async def test_trace_assets_are_hashed_immutable_and_support_head() -> None:
    async with await _client(create_app()) as client:
        page = await client.get("/trace")
        urls = _asset_urls(page.text)
        assert len(urls) == 2
        for url in urls:
            assert re.fullmatch(
                r"/trace/assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.(?:js|css)",
                url,
            )
            response = await client.get(url)
            head = await client.head(url)
            assert response.status_code == head.status_code == 200
            assert head.content == b""
            assert head.headers["content-length"] == str(len(response.content))
            assert response.headers["cache-control"] == (
                "public, max-age=31536000, immutable"
            )
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["content-type"].split(";", 1)[0] in {
                "text/css",
                "text/javascript",
            }


@pytest.mark.parametrize(
    "path",
    [
        "/trace/unknown",
        "/trace/index.html",
        "/trace/assets/unknown.js",
        "/trace/assets/.hidden.js",
        "/trace/assets/%2e%2e/index.html",
        "/trace/assets/%2Fetc%2Fpasswd",
        "/trace/assets/a.js/extra",
        "/trace//",
    ],
)
async def test_trace_rejects_unknown_dot_encoded_and_nested_paths(path: str) -> None:
    async with await _client(create_app()) as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "not_found"}


async def test_missing_or_corrupt_resources_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenResources:
        def load(self) -> object:
            raise UnicodeError("private filesystem detail")

    app = FastAPI()
    install_trace_routes(app, assets=BrokenResources())  # type: ignore[arg-type]
    async with await _client(app) as client:
        response = await client.get("/trace")

    assert response.status_code == 503
    assert response.json() == {"detail": "trace_unavailable"}
    assert "private filesystem detail" not in response.text


async def test_invalid_utf8_asset_is_rejected_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("corrupttrace/__init__.py", "")
        zipped.writestr(
            "corrupttrace/trace/index.html",
            '<div id="root"></div>'
            '<script src="/trace/assets/app-a1.js"></script>'
            '<link href="/trace/assets/app-b2.css" rel="stylesheet">',
        )
        zipped.writestr("corrupttrace/trace/assets/app-a1.js", b"\xff")
        zipped.writestr("corrupttrace/trace/assets/app-b2.css", "body{}")
    monkeypatch.syspath_prepend(str(archive))
    importlib.invalidate_caches()
    try:
        app = FastAPI()
        install_trace_routes(
            app, assets=PackageTraceAssets(package="corrupttrace")
        )
        async with await _client(app) as client:
            response = await client.get("/trace")
    finally:
        sys.modules.pop("corrupttrace", None)

    assert response.status_code == 503
    assert response.json() == {"detail": "trace_unavailable"}


async def test_symlinked_resource_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "linkedtrace"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (package / "trace").symlink_to(outside, target_is_directory=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        app = FastAPI()
        install_trace_routes(app, assets=PackageTraceAssets(package="linkedtrace"))
        async with await _client(app) as client:
            response = await client.get("/trace")
    finally:
        sys.modules.pop("linkedtrace", None)

    assert response.status_code == 503
    assert response.json() == {"detail": "trace_unavailable"}
