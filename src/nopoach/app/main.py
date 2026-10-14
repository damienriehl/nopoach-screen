"""Launch a session-locked server on IPv4 loopback; no external UI resources."""

import secrets
import webbrowser
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from nopoach.app.routes.pages import Session, page, router
from nopoach.market.define import Market
from nopoach.store.workspace import Workspace

Collector = Callable[[Workspace, Market], None]


def create_app(
    *,
    port: int = 8765,
    token: str | None = None,
    collectors: Mapping[str, Collector] | None = None,
) -> FastAPI:
    """Collectors are host-configured lawful public feeds, never account clients.

    Without feed configuration, screen the stored workspace. Each source writes
    its own collection receipts; failures retain other sources and permit targeted
    retry.
    """
    session = Session(collectors=dict(collectors or {}))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        session.executor.shutdown(wait=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.session = session
    launch_token = token or secrets.token_urlsafe(32)
    app.state.launch_token = launch_token
    redeemed = False
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in allowed}

    @app.middleware("http")
    async def local_session(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        nonlocal redeemed
        peer = request.client.host if request.client else ""
        refusal = None
        # 'testclient' is the in-process ASGI transport, never a TCP address.
        if peer not in {"127.0.0.1", "testclient"}:
            refusal = "Loopback requests only"
        elif request.headers.get("host") not in allowed:
            refusal = "Foreign Host refused"
        elif request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") not in origins:
                refusal = "Same-origin requests only"
        supplied = request.query_params.get("token", "")
        response: Response
        if (
            refusal is None
            and request.url.path == "/"
            and request.method == "GET"
            and supplied
            and not redeemed
            and secrets.compare_digest(supplied, launch_token)
        ):
            redeemed = True
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                "nopoach_session", launch_token, httponly=True, samesite="strict"
            )
        else:
            if refusal is None and not secrets.compare_digest(
                request.cookies.get("nopoach_session", ""), launch_token
            ):
                refusal = "Session token required"
            if refusal is not None:
                response = page(
                    request,
                    "refused.html",
                    title="Request refused",
                    reason=refusal,
                    status_code=403,
                    session=None,
                    market=None,
                    result=None,
                )
            else:
                response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    app.include_router(router)
    return app


def launch(*, port: int = 8765) -> None:
    app = create_app(port=port)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False)
    )
    exited = Event()

    def open_ready_browser() -> None:
        while not exited.wait(0.05):
            if server.started:
                webbrowser.open(
                    f"http://127.0.0.1:{port}/?token={app.state.launch_token}"
                )
                return

    opener = Thread(target=open_ready_browser, daemon=True)
    opener.start()
    try:
        server.run()
    finally:
        exited.set()
        opener.join(timeout=2)


if __name__ == "__main__":
    launch()
