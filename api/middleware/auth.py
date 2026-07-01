from __future__ import annotations

import json
import logging
from typing import Optional

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from worker.config import settings

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/auth/refresh",
    "/payments/webhook",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        token = _extract_token(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token de autenticação ausente"},
            )

        user = _verify_token(token)
        if not user:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token inválido ou expirado"},
            )

        request.state.user = user
        return await call_next(request)


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _get_verification_key(token: str) -> Optional[str]:
    if settings.supabase_jwt_secret:
        return settings.supabase_jwt_secret

    try:
        jwks = json.loads(settings.supabase_jwk_public_key)
        kid = jwt.get_unverified_header(token).get("kid")
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(key))
    except Exception as e:
        logger.debug("Falha ao carregar JWK: %s", e)
    return None


def _verify_token(token: str) -> Optional[dict]:
    """
    Valida JWT do Supabase com PyJWT usando HS256 ou JWK ES256.
    """
    try:
        key = _get_verification_key(token)
        if not key:
            logger.error("Chave de verificação JWT não configurada")
            return None

        return jwt.decode(
            token,
            key,
            algorithms=["HS256", "ES256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expirado")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT inválido: %s", e)
        return None


def get_current_user(request: Request) -> dict:
    return request.state.user


def get_user_id(request: Request) -> str:
    return get_current_user(request)["sub"]
