from __future__ import annotations

import httpx

from fastapi import APIRouter, HTTPException, Request

from api.middleware.auth import get_user_id
from api.models.schemas import (
    RefreshTokenRequest,
    RefreshTokenResponse,
    SubscriptionStatus,
    UserProfile,
)
from worker.config import settings
from worker.utils.db import get_client

router = APIRouter()


@router.get("/me", response_model=UserProfile)
def get_profile(request: Request):
    """
    Retorna perfil do usuário autenticado.
    Dados do Supabase Auth + status de assinatura.
    """
    db = get_client()
    user_id = get_user_id(request)

    # Busca dados do usuário na tabela de perfis
    result = db.table("user_profiles").select("*").eq("id", user_id).single().execute()

    profile = result.data
    return UserProfile(
        id=profile["id"],
        email=profile["email"],
        is_premium=profile["is_premium"],
        premium_expires_at=profile.get("premium_expires_at"),
        created_at=profile["created_at"],
    )


@router.get("/subscription", response_model=SubscriptionStatus)
def get_subscription(request: Request):
    """
    Retorna status atual da assinatura.
    Consultado pelo mobile ao abrir o app pra decidir o que mostrar.
    """
    db = get_client()
    user_id = get_user_id(request)

    result = (
        db.table("user_profiles")
        .select(
            "is_premium, premium_platform, premium_product_id, premium_expires_at, premium_auto_renews"
        )
        .eq("id", user_id)
        .single()
        .execute()
    )

    p = result.data
    return SubscriptionStatus(
        is_premium=p["is_premium"],
        platform=p.get("premium_platform"),
        product_id=p.get("premium_product_id"),
        expires_at=p.get("premium_expires_at"),
        auto_renews=p.get("premium_auto_renews"),
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(body: RefreshTokenRequest):
    """
    Renova o access token usando o refresh token do Supabase.

    Usa a anon key (não a service role key): o grant_type=refresh_token
    do GoTrue não exige privilégio elevado — é a mesma chave que os SDKs
    oficiais (supabase-js, etc.) usam para essa chamada. Evita expor a
    service role key (que ignora RLS) num fluxo que não precisa dela.
    """
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/token?grant_type=refresh_token"
    headers = {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, data={"refresh_token": body.refresh_token}, headers=headers)

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Refresh token inválido")

    data = response.json()
    return RefreshTokenResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_in=data["expires_in"],
        token_type=data["token_type"],
    )
