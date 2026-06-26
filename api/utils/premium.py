from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from api.models.schemas import SubscriptionStatus
from worker.utils.db import get_client


def get_subscription(user_id: str) -> SubscriptionStatus:
    """
    Busca status de assinatura do usuário no banco.
    Fonte única de verdade — importar daqui em auth, feed e payments.
    """
    db = get_client()
    result = (
        db.table("user_profiles")
        .select(
            "is_premium, premium_platform, premium_product_id, "
            "premium_expires_at, premium_auto_renews"
        )
        .eq("id", user_id)
        .single()
        .execute()
    )

    if not result.data:
        return SubscriptionStatus(is_premium=False)

    p = result.data

    # Verifica se o premium expirou mesmo com flag true no banco
    # (segurança extra caso o webhook de expiração falhe)
    is_premium = p["is_premium"]
    expires_at: Optional[datetime] = None

    if p.get("premium_expires_at"):
        expires_at = datetime.fromisoformat(p["premium_expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(tz=timezone.utc):
            is_premium = False

    return SubscriptionStatus(
        is_premium=is_premium,
        platform=p.get("premium_platform"),
        product_id=p.get("premium_product_id"),
        expires_at=expires_at,
        auto_renews=p.get("premium_auto_renews"),
    )


def require_premium(request: Request) -> None:
    """
    Levanta 403 se o usuário não tiver assinatura ativa.
    Usar no início de qualquer endpoint premium.
    """
    from api.middleware.auth import get_user_id

    user_id = get_user_id(request)
    status = get_subscription(user_id)
    if not status.is_premium:
        raise HTTPException(
            status_code=403,
            detail="Assinatura necessária para acessar este conteúdo",
        )


def activate_premium(
    user_id: str,
    platform: str,
    product_id: str,
    expires_at: Optional[datetime],
    auto_renews: bool = True,
) -> None:
    """
    Ativa ou renova o premium de um usuário.
    Usado em verify_purchase e no webhook.
    """
    db = get_client()
    db.table("user_profiles").update(
        {
            "is_premium": True,
            "premium_platform": platform,
            "premium_product_id": product_id,
            "premium_expires_at": expires_at.isoformat() if expires_at else None,
            "premium_auto_renews": auto_renews,
        }
    ).eq("id", user_id).execute()


def deactivate_premium(user_id: str) -> None:
    """Desativa premium — usado em EXPIRATION e REFUND."""
    db = get_client()
    db.table("user_profiles").update(
        {
            "is_premium": False,
            "premium_expires_at": None,
            "premium_auto_renews": False,
        }
    ).eq("id", user_id).execute()
