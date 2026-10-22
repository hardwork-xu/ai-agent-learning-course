"""Authenticated support workbench; model work never runs on the HTTP event loop."""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from importlib.resources import files
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agentlab.service import load_config
from agentlab.local_model import strict_json
from .knowledge import KnowledgeError, KnowledgeStore
from .store import DeskError, Store
from .engine import Engine


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewRun(Input):
    question: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    mode: str = Field(default="baseline", pattern=r"^(baseline|local)$")


class Reply(Input):
    text: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)


class Citation(Input):
    id: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=128)
    quote: str = Field(min_length=1, max_length=3000)


class Review(Input):
    expected_version: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=6000)
    citations: list[Citation] = Field(min_length=1, max_length=12)
    decision: str = Field(pattern=r"^(approve|reject)$")
    comment: str = Field(default="", max_length=1000)


class Commit(Input):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class Document(Input):
    document_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
    source: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=128)
    expected_revision: str | None = Field(default=None, max_length=128)


class Withdrawal(Input):
    expected_revision: str = Field(min_length=1, max_length=128)


class WorkerGroup:
    """Fixed worker count; queued work lives in the capacity-limited database."""

    def __init__(self, engine: Engine, count: int):
        self.engine, self.count = engine, count
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.errors = 0

    def _loop(self):
        while not self.stop.is_set():
            try:
                worked = self.engine.run_once()
            except Exception:
                # Raw model/input/database errors must not enter process logs.
                self.errors += 1
                worked = False
            if not worked:
                self.stop.wait(0.1)

    def start(self):
        for index in range(self.count):
            thread = threading.Thread(target=self._loop, name=f"desk-worker-{index}", daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=1)
        # Accepted blocking calls retain their configured deadline. A terminating
        # process leaves durable leases, which a later process safely recovers.


class RateLimit:
    """Single-process admission limit, scoped to authenticated actors."""

    def __init__(self, maximum=120, window=60):
        self.maximum, self.window = maximum, window
        self.rows = {}
        self.lock = threading.Lock()

    def check(self, key):
        now = time.monotonic()
        with self.lock:
            # Bound memory even if the operator frequently rotates identities.
            self.rows = {k: v for k, v in self.rows.items() if now - v[0] < self.window}
            since, used = self.rows.get(key, (now, 0))
            if used >= self.maximum or (key not in self.rows and len(self.rows) >= 4096):
                raise DeskError("rate_limited", 429)
            self.rows[key] = (since, used + 1)


class RequestBoundary:
    """Reject oversized bodies while streaming, before JSON parsing."""

    def __init__(self, app, maximum=160000):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = [(k.lower(), v) for k, v in scope.get("headers", [])]
        lengths = [v for k, v in headers if k == b"content-length"]
        auths = [v for k, v in headers if k == b"authorization"]
        if len(lengths) > 1 or len(auths) > 1:
            return await JSONResponse({"error": "ambiguous_headers"}, 400)(scope, receive, send)
        if lengths:
            try:
                length = int(lengths[0])
                if length < 0 or length > self.maximum:
                    raise ValueError()
            except ValueError:
                return await JSONResponse({"error": "body_too_large"}, 413)(scope, receive, send)
        body = bytearray()
        deadline = time.monotonic() + 5
        while True:
            try:
                message = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return await JSONResponse({"error": "request_timeout"}, 408)(scope, receive, send)
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum:
                return await JSONResponse({"error": "body_too_large"}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        if body and scope.get("path", "").startswith("/api/"):
            try:
                strict_json(body.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                return await JSONResponse({"error": "invalid_json"}, 400)(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(*, data_dir: str | Path, model: str | None = None,
               workers: int = 2, capacity: int = 16,
               allowed_hosts: list[str] | None = None, engine=None) -> FastAPI:
    if type(workers) is not int or not 0 <= workers <= 8:
        raise ValueError("workers must be in [0, 8]")
    directory = Path(data_dir)
    config = directory / "auth.json"
    load_config(config)  # Never invent credentials at server startup.
    store = Store(directory / "desk.sqlite3", capacity=capacity)
    knowledge = KnowledgeStore(directory / "knowledge.sqlite3")
    if engine is None:
        factory = None
        if model:
            from .planner import LocalPlanner
            factory = lambda: LocalPlanner(model=model)
        engine = Engine(store, knowledge, planner_factory=factory)
    worker_group = WorkerGroup(engine, workers)
    rate_limit = RateLimit()

    @asynccontextmanager
    async def lifespan(app):
        worker_group.start()
        try:
            yield
        finally:
            worker_group.close()

    app = FastAPI(title="CaseDesk", version="0.3.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.knowledge, app.state.engine = store, knowledge, engine
    app.state.workers = worker_group
    app.add_middleware(RequestBoundary)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost", "testserver"])

    @app.exception_handler(DeskError)
    async def desk_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    @app.exception_handler(KnowledgeError)
    async def knowledge_error(request, error):
        code = error.code
        status = 409 if "conflict" in code or "revision" in code else 400
        return JSONResponse({"error": code}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Pydantic's standard detail embeds user input; return locations only.
        return JSONResponse({"error": "invalid_input", "fields": [
            list(item["loc"]) for item in error.errors()[:10]]}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        return JSONResponse({"error": "internal_error"}, status_code=500)

    @app.middleware("http")
    async def response_policy(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                valid = (parsed.netloc == request.headers.get("host") and
                         parsed.scheme in {"http", "https"} and not parsed.path and
                         not parsed.query and not parsed.fragment and not parsed.username)
            except ValueError:
                valid = False
            if not valid:
                return JSONResponse({"error": "origin_forbidden"}, status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            # ServerErrorMiddleware re-raises handled exceptions to Uvicorn;
            # catch here so raw provider/input details cannot enter its traceback.
            response = JSONResponse({"error": "internal_error"}, status_code=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    def principal(request: Request):
        import hmac
        try:
            settings = load_config(config)
        except (OSError, ValueError, TypeError, KeyError):
            raise DeskError("auth_config_unavailable", 503) from None
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or not authorization[7:].isascii():
            raise DeskError("unauthorized", 401)
        for item in settings["principals"]:
            if hmac.compare_digest(item["token"], authorization[7:]):
                rate_limit.check((item["tenant"], item["actor"]))
                if request.method in {"POST", "PATCH", "DELETE"} and not settings["write_enabled"]:
                    raise DeskError("writes_disabled", 503)
                return item
        raise DeskError("unauthorized", 401)

    def require(p, role):
        if p["role"] != role:
            raise DeskError("role_forbidden", 403)

    def reader(p):
        return None if p["role"] == "reviewer" else p["actor"]

    @app.get("/health")
    async def health():
        return {"status": "ok", "workers": sum(t.is_alive() for t in worker_group.threads)}

    @app.get("/api/me")
    def me(p=Depends(principal)):
        return {k: p[k] for k in ("tenant", "actor", "role")} | {"local_model_enabled": bool(model)}

    @app.get("/api/openapi.json")
    def specification(p=Depends(principal)):
        return app.openapi()

    @app.post("/api/runs", status_code=202)
    def submit(body: NewRun, p=Depends(principal)):
        require(p, "requester")
        if body.mode == "local" and not model:
            raise DeskError("local_model_not_configured", 409)
        return store.create(p["tenant"], p["actor"], body.question, body.idempotency_key, mode=body.mode)

    @app.get("/api/runs")
    def runs(p=Depends(principal)):
        return {"runs": store.list_runs(p["tenant"], actor=reader(p), limit=50)}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, p=Depends(principal)):
        return store.get(p["tenant"], run_id, actor=reader(p))

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, p=Depends(principal)):
        return {"events": store.events(p["tenant"], run_id, actor=reader(p))}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str, p=Depends(principal)):
        require(p, "requester")
        return store.cancel(p["tenant"], run_id, p["actor"])

    @app.post("/api/runs/{run_id}/reply")
    def reply(run_id: str, body: Reply, p=Depends(principal)):
        require(p, "requester")
        return store.reply(p["tenant"], run_id, p["actor"], body.text, body.expected_version)

    @app.post("/api/runs/{run_id}/review")
    def review(run_id: str, body: Review, p=Depends(principal)):
        require(p, "reviewer")
        return engine.review(tenant=p["tenant"], run_id=run_id, reviewer=p["actor"],
                             **body.model_dump())

    @app.post("/api/runs/{run_id}/commit")
    def commit(run_id: str, body: Commit, p=Depends(principal)):
        require(p, "requester")
        return engine.commit(tenant=p["tenant"], run_id=run_id, actor=p["actor"], **body.model_dump())

    @app.get("/api/metrics")
    def metrics(p=Depends(principal)):
        return store.metrics(p["tenant"], actor=reader(p))

    @app.get("/api/documents")
    def documents(p=Depends(principal)):
        return {"documents": knowledge.list_documents(p["tenant"])}

    @app.get("/api/evidence/{chunk_id}")
    def evidence(chunk_id: str, p=Depends(principal)):
        value = knowledge.get(p["tenant"], chunk_id)
        if value is None:
            raise DeskError("evidence_not_found", 404)
        return value

    @app.post("/api/documents")
    def ingest(body: Document, p=Depends(principal)):
        require(p, "reviewer")
        return knowledge.ingest(tenant=p["tenant"], **body.model_dump())

    @app.post("/api/documents/{document_id}/withdraw")
    def withdraw(document_id: str, body: Withdrawal, p=Depends(principal)):
        require(p, "reviewer")
        return knowledge.withdraw(tenant=p["tenant"], document_id=document_id, **body.model_dump())

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return files("agentlab.desk").joinpath("web", "index.html").read_text(encoding="utf-8")

    @app.get("/assets/{name}")
    async def asset(name: str):
        mime = {"app.js": "text/javascript", "style.css": "text/css"}
        if name not in mime:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return Response(files("agentlab.desk").joinpath("web", name).read_text(encoding="utf-8"),
                        media_type=mime[name])

    return app
