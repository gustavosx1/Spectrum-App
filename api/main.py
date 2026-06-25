from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth.router import router as auth_router
from api.feed.router import router as feed_router
from api.payments.router import router as payments_router
from api.middleware.auth import AuthMiddleware

app = FastAPI(
    title="Spectrum API",
    version="1.0.0",
    docs_url="/docs",  # desabilitar em produção se quiser
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET, POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.add_middleware(AuthMiddleware)

app.include_router(auth_router, prefix="auth", tags=["auth"])
app.include_router(feed_router, prefix="feed", tags=["feed"])
app.include_router(payments_router, prefix="payments", tags=["payments"])


@app.get("/health")
def health():
    return {"health": "ok"}
