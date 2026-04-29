#!/usr/bin/env python3
"""
Local OpenAI-compatible shim between Cursor and Moonshot (Kimi) API.

- Listen as an OpenAI base (e.g. http://127.0.0.1:8765/v1 → Cursor Override URL).
- Forward to Moonshot; merge `thinking` on chat/completions (OpenAI extra_body semantics).
- Stream passthrough (`reasoning_content` unchanged).

Environment:
  MOONSHOT_BASE     Upstream API root WITH /v1. Default: https://api.moonshot.cn/v1
  MOONSHOT_THINKING  "disabled" | "enabled" — set via body key `thinking` if missing.
  SHIM_BIND         Default: 127.0.0.1:8765

Cursor: OpenAI override Base URL → http://127.0.0.1:8765/v1
        API key → your Moonshot sk-...
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route


DEFAULT_UPSTREAM = "https://api.moonshot.cn/v1"
BIND = os.environ.get("SHIM_BIND", "127.0.0.1:8765")
UPSTREAM = os.environ.get("MOONSHOT_BASE", DEFAULT_UPSTREAM).rstrip("/")
THINKING = os.environ.get("MOONSHOT_THINKING", "disabled").strip().lower()


def _upstream_url(local_path: str) -> str:
    """
    Map request path from Cursor onto Moonshot URL.

    Cursor typically requests /v1/chat/completions with base …/v1.
    UPSTREAM is https://host/v1 → target must be …/v1/chat/completions (no double v1).
    """
    p = local_path.strip() or "/"
    if not p.startswith("/"):
        p = "/" + p
    # Normalize: strip leading /v1 if upstream already ends with /v1
    if UPSTREAM.endswith("/v1") and (p == "/v1" or p.startswith("/v1/")):
        p = p[3:] or "/"
    return f"{UPSTREAM}{p}"


def _merge_thinking(body: bytes, path_suffix: str) -> bytes:
    if request_path_is_chat_completions(path_suffix) and body:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
        if isinstance(data, dict) and THINKING in ("disabled", "enabled"):
            # Cursor may send thinking:enabled in body; if env forces disabled, override it.
            data["thinking"] = {"type": THINKING}
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
    return body


def request_path_is_chat_completions(path_for_match: str) -> bool:
    return path_for_match.rstrip("/").endswith("chat/completions")


def _forward_headers(request: Request) -> dict[str, str]:
    skip = {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
    return {k: v for k, v in request.headers.items() if k.lower() not in skip}


async def proxy_request(request: Request) -> Response:
    local_path = request.url.path or "/"
    url = _upstream_url(local_path)
    q = request.url.query
    if q:
        url = f"{url}?{q}"

    body = await request.body()
    suffix = local_path.strip("/")
    if request.method == "POST" and request_path_is_chat_completions(suffix):
        body = _merge_thinking(body, suffix)

    headers = _forward_headers(request)
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                url,
                headers=headers,
                content=body if body else None,
            ),
            stream=True,
        )
    except httpx.RequestError as e:
        return Response(f"Upstream error: {e}", status_code=502)

    hop_skip = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "transfer-encoding",
        "upgrade",
    }
    # Starlette 1.x StreamingResponse expects Mapping[str, str] (items()), not list[tuple].
    hdr_out: dict[str, str] = {}
    for k, v in upstream.headers.items():
        if k.lower() not in hop_skip:
            hdr_out[k] = v

    async def stream_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream.status_code,
        headers=hdr_out,
    )


async def root(_: Request) -> Response:
    """Browser / curl / without path — avoid 500 on GET /."""
    return JSONResponse(
        {
            "service": "moonshot_cursor_shim",
            "health": "/health",
            "cursor_openai_base": f"http://{BIND}/v1",
            "hint": "Use Base URL http://127.0.0.1:8765/v1 in Cursor (see README).",
        }
    )


async def health(_: Request) -> Response:
    return Response(
        json.dumps(
            {
                "ok": True,
                "upstream": UPSTREAM,
                "thinking_default": THINKING,
                "cursor_openai_base": f"http://{BIND}/v1",
            },
            ensure_ascii=False,
        ),
        media_type="application/json",
    )


@asynccontextmanager
async def lifespan(app: Starlette):
    timeout = httpx.Timeout(600.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, http2=False) as client:
        app.state.http_client = client
        yield


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/", root, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", proxy_request, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
        ],
        lifespan=lifespan,
    )


app = build_app()


if __name__ == "__main__":
    import uvicorn

    host, _, port = BIND.partition(":")
    uvicorn.run(
        "shim:app",
        host=host or "127.0.0.1",
        port=int(port or "8765"),
        log_level="info",
    )
