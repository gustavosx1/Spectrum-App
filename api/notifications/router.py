from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request

from api.middleware.auth import get_user_id
from api.models.schemas import (
    RegisterPushTokenRequest,
    RegisterPushTokenResponse,
    UnregisterPushTokenRequest,
    UnregisterPushTokenResponse,
)
from worker.config import settings
from worker.utils.db import get_client

router = APIRouter()

_EXPO_TOKEN_PATTERN = re.compile(r"^(ExponentPushToken|ExpoPushToken)\[[^\]]+\]$")


def _is_valid_expo_token(token: str) -> bool:
    return bool(_EXPO_TOKEN_PATTERN.match((token or "").strip()))


@router.post("/token", response_model=RegisterPushTokenResponse)
def register_push_token(body: RegisterPushTokenRequest, request: Request):
    token = body.expo_push_token.strip()
    platform = body.platform.strip().lower()

    if platform not in {"ios", "android"}:
        raise HTTPException(status_code=400, detail="platform deve ser ios ou android")

    if not _is_valid_expo_token(token):
        raise HTTPException(status_code=400, detail="expo_push_token inválido")

    user_id = get_user_id(request)
    db = get_client()

    payload = {
        settings.push_user_id_column: user_id,
        settings.push_token_column: token,
        "platform": platform,
        settings.push_active_column: True,
    }

    db.table(settings.push_device_table).upsert(
        payload,
        on_conflict=f"{settings.push_user_id_column},{settings.push_token_column}",
    ).execute()

    return RegisterPushTokenResponse(ok=True, message="token registrado")


@router.delete("/token", response_model=UnregisterPushTokenResponse)
def unregister_push_token(body: UnregisterPushTokenRequest, request: Request):
    token = body.expo_push_token.strip()
    if not _is_valid_expo_token(token):
        raise HTTPException(status_code=400, detail="expo_push_token inválido")

    user_id = get_user_id(request)
    db = get_client()

    (
        db.table(settings.push_device_table)
        .update({settings.push_active_column: False})
        .eq(settings.push_user_id_column, user_id)
        .eq(settings.push_token_column, token)
        .execute()
    )

    return UnregisterPushTokenResponse(ok=True, message="token desativado")
