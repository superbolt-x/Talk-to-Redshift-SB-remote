"""
Talk-to-Redshift MCP Server (remote).

Read-only Redshift access for Claude — lists clusters, databases, schemas,
tables, columns, and executes SQL queries.

Auth model: AUTHLESS (Parker-style).
  No OAuth is advertised, so Claude connects with no login step. If MCP_AUTH_TOKEN
  is set, an ASGI gate requires that token on every MCP request — via the connector
  URL (?access_token=...) or an `Authorization: Bearer <token>` header. /health is
  always open. Leave MCP_AUTH_TOKEN empty to run fully open (rely on URL secrecy).
"""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from redshift_mcp.tools import register_tools

logger = logging.getLogger("talk-to-redshift")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

SERVER_URL = os.environ.get("SERVER_URL", "").rstrip("/")
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")  # optional URL/bearer gate
_instructions = (Path(__file__).parent / "INSTRUCTIONS.md").read_text()


def _derive_allowed_hosts() -> list[str]:
    """Hosts allowed through DNS-rebinding protection. Sourced from SERVER_URL
    (scheme optional), Railway's auto-injected RAILWAY_PUBLIC_DOMAIN, and an
    optional MCP_ALLOWED_HOSTS override. Prevents 421 'Invalid Host header'."""
    candidates: list[str] = []
    if SERVER_URL:
        _u = SERVER_URL if "//" in SERVER_URL else "https://" + SERVER_URL
        netloc = urlparse(_u).netloc
        if netloc:
            candidates.append(netloc)
    rpd = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if rpd:
        candidates.append(rpd)
    for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(","):
        if h.strip():
            candidates.append(h.strip())
    seen, out = set(), []
    for h in candidates:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


_allowed_hosts = _derive_allowed_hosts()
if _allowed_hosts:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[x for h in _allowed_hosts for x in (h, f"{h}:*")],
        allowed_origins=[x for h in _allowed_hosts for x in (f"https://{h}", f"http://{h}")],
    )
    logger.info("DNS-rebinding protection ON — allowed hosts: %s", _allowed_hosts)
else:
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    logger.warning("No SERVER_URL / RAILWAY_PUBLIC_DOMAIN — DNS-rebinding protection OFF")

mcp = FastMCP(
    "Talk-to-Redshift",
    instructions=_instructions,
    transport_security=_transport_security,
)
logger.info("Talk-to-Redshift configured — server_url=%s auth_gate=%s",
            SERVER_URL or "(unset)", bool(AUTH_TOKEN))

register_tools(mcp)


class TokenGateMiddleware:
    """Authless-with-a-shared-secret ASGI gate. When a token is configured,
    require it on every HTTP request (from ?access_token=/token or Bearer).
    Correct token → pass through (no auth challenge); otherwise 401."""

    def __init__(self, app, token: str):
        self._app = app
        self._token = token

    def _provided(self, scope) -> str:
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        val = (qs.get("access_token") or qs.get("token") or [""])[0]
        if val:
            return val
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
                if auth.lower().startswith("bearer "):
                    return auth[7:].strip()
        return ""

    async def __call__(self, scope, receive, send):
        if self._token and scope.get("type") == "http":
            if not secrets.compare_digest(self._provided(scope), self._token):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    host      = os.environ.get("MCP_HOST", "0.0.0.0")
    port      = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))

    if transport != "streamable-http":
        mcp.run(transport=transport)
        return

    raw_app = mcp.streamable_http_app()
    gated_app = TokenGateMiddleware(raw_app, token=AUTH_TOKEN)

    @asynccontextmanager
    async def lifespan(_app):
        async with raw_app.router.lifespan_context(_app):
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "transport": transport,
            "auth_gate": bool(AUTH_TOKEN),
        })

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health),
            Mount("/", app=gated_app),
        ],
    )
    logger.info("Listening on %s:%s — auth_gate=%s", host, port, bool(AUTH_TOKEN))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
