"""FastAPI: GET /health and POST /v1/ask. Answers come only from handle()."""

from __future__ import annotations

import logging
import os
from typing import Any

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ingest.embed_index import (
    DEFAULT_INDEX_DIR,
    boot_retriever,
    encoder_is_cached,
    open_collection,
)
from src.format import winning_citation
from src.generate import pii_block_for_gemini, policy_block_for_gemini
from src.guard import classify
from src.latency import LatencyError, measure_latency
from src.pipeline import handle
from src.refuse import INCOMPLETE_EMPTY
from src.schemes import AMFI_INVESTOR_URL
from src.scheduler import (
    SchedulerError,
    create_schedule,
    delete_schedule,
    get_schedule,
    load_runs,
    load_schedules,
    public_schedule,
    start_loop,
    start_run,
    stop_loop,
    update_schedule,
)

load_dotenv()

logger = logging.getLogger("api")

DEFAULT_FRONTEND_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)

CORPUS_UNAVAILABLE = "The Groww corpus is unavailable right now."


def frontend_origins() -> list[str]:
    raw = os.environ.get("FRONTEND_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_FRONTEND_ORIGINS)
    return [part.strip() for part in raw.split(",") if part.strip()]


def index_ready(index_dir: Any | None = None) -> bool:
    """True when the Chroma store can be opened and contains at least one chunk."""
    folder = index_dir or DEFAULT_INDEX_DIR
    try:
        collection = open_collection(folder, reset=False)
        return int(collection.count()) > 0
    except Exception:
        return False


def _ask_payload(
    *,
    text: str,
    intent: str | None,
    scheme_id: str | None,
    topic: str | None,
    source_url: str | None,
    as_of_date: str | None,
    pii_blocked: bool,
) -> dict[str, Any]:
    return {
        "text": text,
        "intent": intent,
        "scheme_id": scheme_id,
        "topic": topic,
        "source_url": source_url,
        "as_of_date": as_of_date,
        "pii_blocked": pii_blocked,
    }


def _log_ask(
    *,
    status: int,
    intent: str | None,
    scheme_id: str | None,
    topic: str | None,
    pii_blocked: bool,
) -> None:
    # Never log the raw query. Identifiers must not appear in server logs.
    logger.info(
        "ask status=%s intent=%s scheme_id=%s topic=%s pii_blocked=%s",
        status,
        intent,
        scheme_id,
        topic,
        pii_blocked,
    )


class AskRequest(BaseModel):
    query: str = ""
    extractive: bool = Field(default=False)


class AskResponse(BaseModel):
    text: str
    intent: str | None
    scheme_id: str | None
    topic: str | None
    source_url: str | None
    as_of_date: str | None
    pii_blocked: bool


class HealthResponse(BaseModel):
    ok: bool
    index_ready: bool
    encoder_ready: bool


class LatencyRequest(BaseModel):
    query: str = ""
    mode: str = "full"


class LayerTiming(BaseModel):
    id: str
    label: str
    group: str
    ms: float
    skipped: bool
    detail: str | None = None


class LatencyResponse(BaseModel):
    ok: bool
    index_ready: bool
    encoder_cached: bool
    mode: str
    probe: str
    intent: str | None
    scheme_id: str | None
    topic: str | None
    writer: str
    chunks: int
    pii_blocked: bool
    layers: list[LayerTiming]
    server_ms: float


class ScheduleCreate(BaseModel):
    name: str = "Corpus refresh"
    times: list[str] = Field(default_factory=list)


class SchedulePatch(BaseModel):
    name: str | None = None
    times: list[str] | None = None
    enabled: bool | None = None


def encoder_ready() -> bool:
    """True when MiniLM is already loaded in this process."""
    return encoder_is_cached()


def ensure_index() -> None:
    if not boot_retriever():
        logger.info("Groww index is not ready")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_loop()
    yield
    stop_loop()


app = FastAPI(title="Groww FAQ API", version="0.8.0", lifespan=lifespan)
ensure_index()
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(),
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Server-Timing"],
)


@app.middleware("http")
async def allow_resource_timing(request: Any, call_next: Any) -> Any:
    """Let the Vercel page split DNS / TLS / TTFB on cross-origin /latency."""
    response = await call_next(request)
    response.headers["Timing-Allow-Origin"] = "*"
    return response


@app.get("/")
def root() -> dict[str, object]:
    return {
        "service": "Groww HDFC Limited FAQ",
        "docs": "/docs",
        "health": "/health",
        "ask": "POST /v1/ask",
        "latency": "GET /latency",
        "scheduler": "GET /v1/schedules",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> dict[str, bool]:
    return {
        "ok": True,
        "index_ready": index_ready(),
        "encoder_ready": encoder_ready(),
    }


def _latency_response(mode: str, query: str | None) -> JSONResponse:
    try:
        body, timing = measure_latency(
            mode=mode,
            query=query,
            check_index=index_ready,
        )
    except LatencyError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "text": str(exc)})
    headers = {
        "Server-Timing": timing,
        "Timing-Allow-Origin": "*",
        "Cache-Control": "no-store",
    }
    status = 200 if body.get("ok") else 503
    logger.info(
        "latency status=%s mode=%s writer=%s server_ms=%s encoder_cached=%s pii_blocked=%s",
        status,
        body.get("mode"),
        body.get("writer"),
        body.get("server_ms"),
        body.get("encoder_cached"),
        body.get("pii_blocked"),
    )
    return JSONResponse(status_code=status, content=body, headers=headers)


@app.get("/latency", response_model=LatencyResponse)
def latency_get(mode: str = "full") -> JSONResponse:
    return _latency_response(mode, None)


@app.post("/latency", response_model=LatencyResponse)
def latency_post(body: LatencyRequest) -> JSONResponse:
    query = body.query if isinstance(body.query, str) else ""
    return _latency_response(body.mode or "full", query.strip() or None)


def _scheduler_error(exc: SchedulerError, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "text": str(exc)})


@app.get("/v1/schedules")
def schedules_list() -> dict[str, object]:
    rows = [public_schedule(item) for item in load_schedules()]
    return {"ok": True, "timezone": "Asia/Kolkata", "schedules": rows}


@app.post("/v1/schedules", response_model=None)
def schedules_create(body: ScheduleCreate) -> dict[str, object] | JSONResponse:
    try:
        row = create_schedule(body.name, body.times)
    except SchedulerError as exc:
        return _scheduler_error(exc)
    return {"ok": True, "schedule": row}


@app.patch("/v1/schedules/{schedule_id}", response_model=None)
def schedules_patch(schedule_id: str, body: SchedulePatch) -> dict[str, object] | JSONResponse:
    try:
        row = update_schedule(
            schedule_id,
            name=body.name,
            times=body.times,
            enabled=body.enabled,
        )
    except SchedulerError as exc:
        return _scheduler_error(exc)
    if row is None:
        return _scheduler_error(SchedulerError("schedule not found"), 404)
    return {"ok": True, "schedule": row}


@app.delete("/v1/schedules/{schedule_id}", response_model=None)
def schedules_delete(schedule_id: str) -> dict[str, object] | JSONResponse:
    if not delete_schedule(schedule_id):
        return _scheduler_error(SchedulerError("schedule not found"), 404)
    return {"ok": True}


@app.post("/v1/schedules/{schedule_id}/run", response_model=None)
def schedules_run_now(schedule_id: str) -> dict[str, object] | JSONResponse:
    if get_schedule(schedule_id) is None:
        return _scheduler_error(SchedulerError("schedule not found"), 404)
    try:
        run = start_run(schedule_id, trigger="manual")
    except SchedulerError as exc:
        return _scheduler_error(exc)
    return {"ok": True, "run": run}


@app.get("/v1/scheduler/runs")
def scheduler_runs(limit: int = 50, schedule_id: str | None = None) -> dict[str, object]:
    rows = load_runs(limit=limit, schedule_id=schedule_id)
    return {"ok": True, "runs": rows}


@app.post("/v1/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse | JSONResponse:
    query = body.query if isinstance(body.query, str) else ""
    decision = classify(query)
    if decision.reason == "empty":
        payload = _ask_payload(
            text=INCOMPLETE_EMPTY,
            intent=decision.intent,
            scheme_id=None,
            topic=None,
            source_url=None,
            as_of_date=None,
            pii_blocked=False,
        )
        _log_ask(
            status=400,
            intent=decision.intent,
            scheme_id=None,
            topic=None,
            pii_blocked=False,
        )
        return JSONResponse(status_code=400, content=payload)

    if not index_ready():
        payload = _ask_payload(
            text=CORPUS_UNAVAILABLE,
            intent=None,
            scheme_id=None,
            topic=None,
            source_url=None,
            as_of_date=None,
            pii_blocked=False,
        )
        _log_ask(
            status=503,
            intent=None,
            scheme_id=None,
            topic=None,
            pii_blocked=False,
        )
        return JSONResponse(status_code=503, content=payload)

    result = handle(query, force_extractive=body.extractive)
    pii_blocked = pii_block_for_gemini(query) is not None
    # Citation fields belong to the chunk that wrote the answer, not a retrieve leftover.
    if result.intent == "advisory":
        source_url, as_of_date = AMFI_INVESTOR_URL, None
    elif result.intent == "catalog":
        source_url = None
        _, as_of_date = winning_citation(result.chunks[0] if result.chunks else None)
    elif policy_block_for_gemini(query) is None:
        source_url, as_of_date = winning_citation(result.chunks[0] if result.chunks else None)
    else:
        source_url, as_of_date = None, None
    payload = _ask_payload(
        text=result.text,
        intent=result.intent,
        scheme_id=result.scheme_id,
        topic=result.topic,
        source_url=source_url,
        as_of_date=as_of_date,
        pii_blocked=pii_blocked,
    )
    _log_ask(
        status=200,
        intent=result.intent,
        scheme_id=result.scheme_id,
        topic=result.topic,
        pii_blocked=pii_blocked,
    )
    return payload
