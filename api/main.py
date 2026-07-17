from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth.router import router as auth_router
from api.feed.router import router as feed_router
from api.notifications.router import router as notifications_router
from api.payments.router import router as payments_router
from api.middleware.auth import AuthMiddleware
from worker.config import settings


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        extra_fields = {k: v for k, v in record.__dict__.items() if k not in {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
        }}
        payload.update(extra_fields)
        try:
            return json.dumps(payload, default=str)
        except Exception:
            return super().format(record)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def error_response(status_code: int, detail: str, path: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"status": status_code, "detail": detail, "path": path}},
    )


app = FastAPI(
    title="Spectrum API",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

setup_logging()

# Ordem de registro importa: no Starlette, o middleware adicionado por
# último fica por fora de tudo. Auth precisa estar por dentro do CORS
# (senão um preflight OPTIONS bloqueado pelo Auth nunca recebe headers
# de CORS e o browser derruba a resposta) e por dentro do logging de
# métricas (senão requisições barradas pelo Auth não aparecem nos logs
# de request.complete).
app.add_middleware(AuthMiddleware)


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    start = time.time()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = round((time.time() - start) * 1000, 2)
        status_code = getattr(response, "status_code", 500)
        logging.getLogger("api.request").info(
            "request.complete",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )


cors_origins = settings.cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials="*" not in cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logging.getLogger("api.error").warning(
        "http_exception",
        extra={
            "path": request.url.path,
            "status_code": exc.status_code,
            "detail": exc.detail,
        },
    )
    return error_response(exc.status_code, str(exc.detail), request.url.path)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.getLogger("api.error").error(
        "unhandled_exception",
        extra={
            "path": request.url.path,
            "detail": str(exc),
        },
        exc_info=exc,
    )
    return error_response(500, "Internal server error", request.url.path)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(feed_router, prefix="/feed", tags=["feed"])
app.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
app.include_router(payments_router, prefix="/payments", tags=["payments"])


@app.get("/health")
def health():
    return {"health": "ok"}
