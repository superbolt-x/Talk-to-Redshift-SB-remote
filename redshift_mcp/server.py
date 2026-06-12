"""
Talk-to-Redshift MCP Server.

Read-only Redshift access for Claude — lists clusters, databases, schemas,
tables, columns, and executes SELECT queries. Write operations are blocked.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from redshift_mcp.tools import register_tools

logger = logging.getLogger("talk-to-redshift")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

_server_url   = os.environ.get("SERVER_URL", "").rstrip("/")
_auth_token   = os.environ.get("MCP_AUTH_TOKEN", "")
_instructions = (Path(__file__).parent / "INSTRUCTIONS.md").read_text()

if _server_url and _auth_token:
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
    from mcp.server.transport_security import TransportSecuritySettings
    from redshift_mcp.oauth import SimpleMCPOAuthProvider

    _oauth_provider = SimpleMCPOAuthProvider(auth_token=_auth_token)

    _auth_settings = AuthSettings(
        issuer_url=_server_url,
        resource_server_url=f"{_server_url}/mcp",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp"],
            default_scopes=["mcp"],
        ),
    )

    _hostname = urlparse(_server_url).netloc
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[_hostname, f"{_hostname}:*"],
        allowed_origins=[_server_url, f"{_server_url}:*"],
    )

    mcp = FastMCP(
        "Talk-to-Redshift",
        instructions=_instructions,
        auth=_auth_settings,
        auth_server_provider=_oauth_provider,
        transport_security=_transport_security,
    )
    logger.info("OAuth enabled — issuer: %s  resource: %s/mcp", _server_url, _server_url)
else:
    _oauth_provider = None
    mcp = FastMCP(
        "Talk-to-Redshift",
        instructions=_instructions,
    )
    logger.warning("OAuth disabled — set SERVER_URL and MCP_AUTH_TOKEN to enable.")

register_tools(mcp)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    host      = os.environ.get("MCP_HOST", "0.0.0.0")
    port      = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))

    if transport != "streamable-http":
        mcp.run(transport=transport)
        return

    _raw_mcp_app = mcp.streamable_http_app()

    # Fix for Railway: Content-Type on /token must be application/x-www-form-urlencoded
    # but some clients send it without the header — this middleware patches the receive.
    from starlette.types import ASGIApp, Receive, Scope, Send

    class _GrantTypeFixMiddleware:
        def __init__(self, app: ASGIApp) -> None:
            self._app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http" and scope.get("path", "").endswith("/token"):
                body_sent = False

                async def _patched_receive():
                    nonlocal body_sent
                    msg = await receive()
                    if msg["type"] == "http.request" and not body_sent:
                        body = msg.get("body", b"")
                        body_sent = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return {"type": "http.disconnect"}

                await self._app(scope, _patched_receive, send)
            else:
                await self._app(scope, receive, send)

    mcp_app = _GrantTypeFixMiddleware(_raw_mcp_app)

    @asynccontextmanager
    async def lifespan(_app):
        async with _raw_mcp_app.router.lifespan_context(_app):
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "transport": transport,
            "oauth": bool(_oauth_provider),
        })

    async def oauth_approve(request: Request) -> HTMLResponse | RedirectResponse:
        if _oauth_provider is None:
            return HTMLResponse("OAuth not configured.", status_code=503)

        pending_id = request.query_params.get("pending_id", "")

        if request.method == "GET":
            return HTMLResponse(_oauth_provider.render_approve_form(pending_id))

        form = await request.form()
        passphrase = str(form.get("passphrase", ""))
        pending_id = str(form.get("pending_id", pending_id))
        ok, redirect_url, error = _oauth_provider.handle_approval(pending_id, passphrase)

        if ok and redirect_url:
            return RedirectResponse(redirect_url, status_code=302)

        return HTMLResponse(
            _oauth_provider.render_approve_form(pending_id, error or "Authorization failed."),
            status_code=400,
        )

    routes = [
        Route("/health", health),
        Route("/oauth/approve", oauth_approve, methods=["GET", "POST"]),
        Mount("/", app=mcp_app),
    ]

    app = Starlette(lifespan=lifespan, routes=routes)
    logger.info("Listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
