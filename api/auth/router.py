from __future__ import annotations

import httpx

from fastapi import APIRouter, HTTPException, Request

from api.middleware.auth import get_user_id
from api.models.schemas import (
    DeleteAccountResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    SubscriptionStatus,
    UserProfile,
)
from worker.config import settings
from worker.utils.db import get_client

router = APIRouter()


def _get_admin_supabase_client():
    from supabase import create_client

    if not settings.supabase_service_role_key:
        raise HTTPException(
            status_code=503,
            detail="SUPABASE_SERVICE_ROLE_KEY não configurada",
        )

    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def _delete_supabase_auth_user(user_id: str) -> None:
    _get_admin_supabase_client()
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.delete(url, headers=headers)

    if response.status_code not in {200, 204}:
        raise HTTPException(
            status_code=response.status_code,
            detail="Falha ao remover usuário do Supabase Auth",
        )


def _cleanup_user_data(user_id: str) -> None:
    admin_db = _get_admin_supabase_client()

    # Limpa dados de app antes/depois do delete no Auth. Isso evita lixo
    # em tabelas que não necessariamente usam cascade no Supabase.
    for table_name, column_name in (
        ("device_push_tokens", "user_id"),
        ("redeemed_purchases", "user_id"),
        ("user_profiles", "id"),
    ):
        admin_db.table(table_name).delete().eq(column_name, user_id).execute()


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


@router.delete("/delete", response_model=DeleteAccountResponse)
async def delete_account(request: Request):
    """
    Remove a conta autenticada.

    Fluxo:
    1. limpa dados de app ligados ao usuário
    2. remove o usuário do Supabase Auth
    3. retorna confirmação para o frontend
    """
    user_id = get_user_id(request)

    # Se a remoção do Auth falhar, os dados ainda ficam intactos até que a
    # operação seja concluída; isso evita deletar tudo e deixar a conta viva.
    await _delete_supabase_auth_user(user_id)
    _cleanup_user_data(user_id)

    return DeleteAccountResponse(ok=True, message="Conta excluída com sucesso")
