from __future__ import annotations

import importlib
import re
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from specpilot.api.app import create_app
from specpilot.api.static import PackageTraceAssets

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_trace_assets_work_outside_repository_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://installed"
    ) as client:
        page = await client.get("/trace")
        url = re.search(r'src="(/trace/assets/[^"]+\.js)"', page.text)
        assert url is not None
        asset = await client.get(url.group(1))

    assert page.status_code == 200
    assert asset.status_code == 200
    assert asset.content


async def test_package_loader_reads_trace_bundle_from_zip_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "bundle.zip"
    index = (
        '<div id="root"></div><script src="/trace/assets/app-a1.js"></script>'
        '<link href="/trace/assets/app-b2.css" rel="stylesheet">'
    )
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("ziptrace/__init__.py", "")
        zipped.writestr("ziptrace/trace/index.html", index)
        zipped.writestr("ziptrace/trace/assets/app-a1.js", "export {}")
        zipped.writestr("ziptrace/trace/assets/app-b2.css", "body{}")
    monkeypatch.syspath_prepend(str(archive))
    importlib.invalidate_caches()
    try:
        bundle = PackageTraceAssets(package="ziptrace").load()
    finally:
        sys.modules.pop("ziptrace", None)

    assert bundle.index == index.encode()
    assert bundle.assets == {"app-a1.js": b"export {}", "app-b2.css": b"body{}"}
