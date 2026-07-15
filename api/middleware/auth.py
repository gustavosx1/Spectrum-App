from __future__ import annotations

import json
import logging
from typing import Any, Optional

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
    "/feed/outlets",
    "/feed/topicsfree",
    "/payments/webhook",
}

PUBLIC_PATH_PREFIXES = (
    "/feed/topicsfree/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_public_path(request.url.path):
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


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _get_jwk_verification_key(token: str) -> Optional[Any]:
    if not settings.supabase_jwk_public_key:
        return None

    try:
        jwks = json.loads(settings.supabase_jwk_public_key)
        kid = jwt.get_unverified_header(token).get("kid")
        keys = jwks.get("keys", [])
        for key in keys:
            if key.get("kid") == kid:
                return jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(key))

        # Fallback when kid is absent and only one key is configured.
        if len(keys) == 1:
            return jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(keys[0]))
    except Exception as e:
        logger.debug("Falha ao carregar JWK: %s", e)
    return None


def _get_verification_material(token: str) -> tuple[Optional[Any], list[str]]:
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
    except Exception:
        return None, []

    if alg.startswith("HS") and settings.supabase_jwt_secret:
        return settings.supabase_jwt_secret, [alg]

    if alg.startswith("ES"):
        jwk_key = _get_jwk_verification_key(token)
        if jwk_key:
            return jwk_key, [alg]

    # Backward-compatible fallback during migration.
    if settings.supabase_jwt_secret:
        return settings.supabase_jwt_secret, ["HS256"]

    jwk_key = _get_jwk_verification_key(token)
    if jwk_key:
        return jwk_key, ["ES256"]

    return None, []


def _verify_token(token: str) -> Optional[dict]:
    """
    Valida JWT do Supabase com PyJWT usando HS256 ou JWK ES256.
    """
    try:
        key, algorithms = _get_verification_material(token)
        if not key or not algorithms:
            logger.error("Chave de verificação JWT não configurada")
            return None

        verify_aud = bool(settings.jwt_expected_audience)
        issuer = settings.jwt_expected_issuer or f"{settings.supabase_url.rstrip('/')}/auth/v1"

        decode_kwargs: dict[str, Any] = {
            "algorithms": algorithms,
            "options": {"verify_aud": verify_aud},
            "issuer": issuer,
        }
        if verify_aud:
            decode_kwargs["audience"] = settings.jwt_expected_audience

        return jwt.decode(token, key, **decode_kwargs)
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expirado")
        return None
    except jwt.InvalidIssuerError:
        logger.debug("Issuer JWT inválido")
        return None
    except jwt.InvalidAudienceError:
        logger.debug("Audience JWT inválido")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT inválido: %s", e)
        return None


def get_current_user(request: Request) -> dict:
    return request.state.user


def get_user_id(request: Request) -> str:
    return get_current_user(request)["sub"]
