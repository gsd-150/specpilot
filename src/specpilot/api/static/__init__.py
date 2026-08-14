"""Serve the trace UI from immutable, installed package resources."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from specpilot.demo.scenarios import PublicDemoScenario

_ASSET_REFERENCE = re.compile(
    rb'(?:src|href)="(/trace/assets/([A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.(?:js|css)))"'
)
_ROOT = b'<div id="root"></div>'
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; font-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


@dataclass(frozen=True, slots=True)
class TraceBundle:
    index: bytes
    assets: dict[str, bytes]


class TraceAssets(Protocol):
    def load(self) -> TraceBundle: ...


@dataclass(frozen=True, slots=True)
class PackageTraceAssets:
    """Read without converting resources to host paths, including zip imports."""

    package: str = "specpilot.api.static"

    def load(self) -> TraceBundle:
        root = resources.files(self.package).joinpath("trace")
        _reject_symlink(root)
        index_resource = root.joinpath("index.html")
        _reject_symlink(index_resource)
        index = index_resource.read_bytes()
        index.decode("utf-8", errors="strict")
        if index.count(_ROOT) != 1:
            raise ValueError("trace root contract mismatch")
        names = {
            match.group(2).decode("ascii")
            for match in _ASSET_REFERENCE.finditer(index)
        }
        if {name.rsplit(".", 1)[-1] for name in names} != {"js", "css"}:
            raise ValueError("trace asset contract mismatch")
        loaded: dict[str, bytes] = {}
        _reject_symlink(root.joinpath("assets"))
        for name in names:
            asset = root.joinpath("assets", name)
            _reject_symlink(asset)
            content = asset.read_bytes()
            if not content:
                raise ValueError("empty trace asset")
            content.decode("utf-8", errors="strict")
            loaded[name] = content
        return TraceBundle(index=index, assets=loaded)


def install_trace_routes(
    app: FastAPI,
    *,
    source_manifest_id: str = "",
    corpus_manifest_id: str = "",
    profile: Literal["fixture", "real"] = "real",
    demo_scenarios: Sequence[PublicDemoScenario] = (),
    assets: TraceAssets | None = None,
) -> None:
    """Install only the exact trace entrypoints, with a sanitized failure surface."""

    source = _binding_hash(source_manifest_id)
    corpus = _binding_hash(corpus_manifest_id)
    public_scenarios = html.escape(
        json.dumps(
            [item.model_dump(mode="json") for item in demo_scenarios],
            separators=(",", ":"),
            sort_keys=True,
        ),
        quote=True,
    )
    loader = assets or PackageTraceAssets()

    def load() -> TraceBundle | None:
        try:
            return loader.load()
        except Exception:
            return None

    async def index(request: Request) -> Response:
        bundle = load()
        if bundle is None:
            return _unavailable()
        binding = (
            f'<div id="root" data-source-manifest-id="{source}" '
            f'data-corpus-manifest-id="{corpus}" data-profile="{profile}" '
            f'data-demo-scenarios="{public_scenarios}"></div>'
        ).encode()
        body = bundle.index.replace(_ROOT, binding)
        return HTMLResponse(
            body if request.method == "GET" else b"",
            headers=_headers(index=True, length=len(body)),
        )

    async def asset(request: Request, filename: str) -> Response:
        if (
            filename.startswith(".")
            or "/" in filename
            or "\\" in filename
            or re.fullmatch(
                r"[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.(?:js|css)", filename
            )
            is None
        ):
            return _not_found()
        bundle = load()
        if bundle is None:
            return _unavailable()
        body = bundle.assets.get(filename)
        if body is None:
            return _not_found()
        media_type = "text/css" if filename.endswith(".css") else "text/javascript"
        return Response(
            body if request.method == "GET" else b"",
            media_type=media_type,
            headers=_headers(index=False, length=len(body)),
        )

    async def missing(path: str) -> Response:
        return _not_found()

    app.add_api_route("/trace", index, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(
        "/trace/", index, methods=["GET", "HEAD"], include_in_schema=False
    )
    app.add_api_route(
        "/trace/assets/{filename}",
        asset,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/trace/{path:path}", missing, methods=["GET", "HEAD"], include_in_schema=False
    )


def _headers(*, index: bool, length: int) -> dict[str, str]:
    return {
        "Cache-Control": "no-store" if index else "public, max-age=31536000, immutable",
        "Content-Length": str(length),
        "Content-Security-Policy": _CSP,
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _not_found() -> JSONResponse:
    return JSONResponse(
        {"detail": "not_found"},
        status_code=404,
        headers=_headers(index=True, length=22),
    )


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"detail": "trace_unavailable"}, status_code=503, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": _CSP,
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        }
    )


def _reject_symlink(resource: Traversable) -> None:
    if isinstance(resource, Path) and resource.is_symlink():
        raise ValueError("trace resource may not be a symlink")


def _binding_hash(value: str) -> str:
    if value == "":
        return value
    if _SHA256.fullmatch(value) is None:
        # Invalid deployment state must not be reflected into an executable page.
        return ""
    return html.escape(value, quote=True)


__all__ = ["PackageTraceAssets", "TraceBundle", "install_trace_routes"]
