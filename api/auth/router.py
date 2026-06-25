from __future__ import annotations

from fastapi import APIRouter, Request

from api.middleware.auth import get_user_id
from api.models.schemas import SubscriptionStatus, UserProfile
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
