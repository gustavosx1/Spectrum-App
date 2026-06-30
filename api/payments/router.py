from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Header, HTTPException, Request

from api.middleware.auth import get_user_id
from api.models.schemas import (
    PurchaseVerifyRequest,
    PurchaseVerifyResponse,
    SubscriptionStatus,
)
from api.utils.premium import activate_premium, deactivate_premium, get_subscription
from worker.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/verify", response_model=PurchaseVerifyResponse)
async def verify_purchase(body: PurchaseVerifyRequest, request: Request):
    """
    Verifica compra feita nas lojas e ativa o premium.
    Chamado pelo mobile logo após o usuário completar a compra.
    """
    user_id = get_user_id(request)

    if body.platform == "ios":
        result = await _verify_apple(body.receipt_token)
    elif body.platform == "android":
        result = await _verify_google(body.receipt_token, body.product_id)
    else:
        raise HTTPException(
            status_code=400, detail="Platform deve ser 'ios' ou 'android'"
        )

    if result["is_valid"]:
        activate_premium(
            user_id=user_id,
            platform=body.platform,
            product_id=body.product_id,
            expires_at=result.get("expires_at"),
        )

    return PurchaseVerifyResponse(
        is_valid=result["is_valid"],
        is_premium=result["is_valid"],
        expires_at=result.get("expires_at"),
        message="Assinatura ativada com sucesso"
        if result["is_valid"]
        else "Compra inválida",
    )


@router.get("/status", response_model=SubscriptionStatus)
def subscription_status(request: Request):
    """Status atual da assinatura."""
    return get_subscription(get_user_id(request))


@router.post("/webhook")
async def payment_webhook(
    request: Request,
    x_revenuecat_signature: str = Header(default=None),
):
    """
    Webhook do RevenueCat — notifica mudanças de assinatura (iOS + Android).

    Eventos tratados:
    - INITIAL_PURCHASE / RENEWAL → ativa premium
    - CANCELLATION               → desativa auto-renovação
    - EXPIRATION / REFUND        → desativa premium
    """
    body = await request.body()

    if not _verify_revenuecat_signature(body, x_revenuecat_signature):
        raise HTTPException(status_code=401, detail="Assinatura do webhook inválida")

    payload = json.loads(body)
    event = payload.get("event", {})
    event_type = event.get("type")
    app_user_id = event.get("app_user_id")  # user_id do Supabase

    if not app_user_id:
        return {"status": "ignored"}

    if event_type in ("INITIAL_PURCHASE", "RENEWAL"):
        expires_at = _parse_ms(event.get("expiration_at_ms"))
        activate_premium(
            user_id=app_user_id,
            platform=event.get("store", "").lower(),
            product_id=event.get("product_id", ""),
            expires_at=expires_at,
            auto_renews=True,
        )
        logger.info("Premium ativado/renovado: %s até %s", app_user_id, expires_at)

    elif event_type == "CANCELLATION":
        from worker.utils.db import get_client

        get_client().table("user_profiles").update({"premium_auto_renews": False}).eq(
            "id", app_user_id
        ).execute()
        logger.info("Auto-renovação cancelada: %s", app_user_id)

    elif event_type in ("EXPIRATION", "REFUND"):
        deactivate_premium(app_user_id)
        logger.info("Premium desativado (%s): %s", event_type, app_user_id)

    return {"status": "ok"}


# ── Requisições externas com retries ─────────────────────────────────────────


async def _request_with_retries(
    method: str,
    url: str,
    retries: int = 2,
    timeout_seconds: int = 30,
    **kwargs: Any,
) -> httpx.Response:
    last_exception: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.request(method, url, **kwargs)
            if response.status_code < 500:
                return response
            last_exception = RuntimeError(
                f"Server error {response.status_code} on {url}"
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exception = exc
            logger.warning(
                "external_request.retry",
                extra={"url": url, "attempt": attempt, "error": str(exc)},
            )
            if attempt < retries:
                await asyncio.sleep(1)
                continue
            raise
        if attempt < retries:
            await asyncio.sleep(1)
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected error during external request")


# ── Verificação Apple ─────────────────────────────────────────────────────────


async def _verify_apple(receipt: str) -> dict:
    """
    Verifica receipt do iOS.
    Tenta produção primeiro, cai pra sandbox se receber status 21007.
    """
    payload = {
        "receipt-data": receipt,
        "password": settings.apple_shared_secret,
        "exclude-old-transactions": True,
    }
    urls = [
        "https://buy.itunes.apple.com/verifyReceipt",
        "https://sandbox.itunes.apple.com/verifyReceipt",
    ]
    for url in urls:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
        status = data.get("status", -1)

        if status == 21007:
            continue  # receipt de sandbox, tenta sandbox endpoint
        if status != 0:
            logger.warning("Apple receipt inválido, status: %s", status)
            return {"is_valid": False}

        receipts = data.get("latest_receipt_info", [])
        if not receipts:
            return {"is_valid": False}

        latest = max(receipts, key=lambda r: int(r.get("expires_date_ms", 0)))
        expires_at = _parse_ms(int(latest.get("expires_date_ms", 0)))

        if not expires_at or expires_at < datetime.now(tz=timezone.utc):
            return {"is_valid": False}

        return {"is_valid": True, "expires_at": expires_at}

    return {"is_valid": False}


# ── Verificação Google ────────────────────────────────────────────────────────


async def _verify_google(purchase_token: str, product_id: str) -> dict:
    """Verifica purchase token do Android via Google Play Developer API."""
    access_token = await _google_access_token()
    if not access_token:
        return {"is_valid": False}

    url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{settings.android_package_name}/purchases/subscriptions/"
        f"{product_id}/tokens/{purchase_token}"
    )
    resp = await _request_with_retries(
        "GET",
        url,
        timeout_seconds=30,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if resp.status_code != 200:
        logger.warning(
            "Google purchase inválido",
            extra={"url": url, "status_code": resp.status_code},
        )
        return {"is_valid": False}

    data = resp.json()
    if data.get("paymentState") not in (1, 2):  # 1=pago, 2=trial
        return {"is_valid": False}

    expires_at = _parse_ms(int(data.get("expiryTimeMillis", 0)))
    if not expires_at or expires_at < datetime.now(tz=timezone.utc):
        return {"is_valid": False}

    return {"is_valid": True, "expires_at": expires_at}


async def _google_access_token() -> Optional[str]:
    """Obtém access token OAuth2 via Google service account."""
    try:
        sa = json.loads(settings.google_service_account_json)
        now = int(time.time())
        claim = {
            "iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/androidpublisher",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }
        # PyJWT pra assinar com RS256
        signed = pyjwt.encode(claim, sa["private_key"], algorithm="RS256")
        resp = await _request_with_retries(
            "POST",
            "https://oauth2.googleapis.com/token",
            timeout_seconds=30,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed,
            },
        )
        return resp.json().get("access_token")
    except Exception as e:
        logger.error("Erro ao obter token Google: %s", e)
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_ms(ms: Optional[int]) -> Optional[datetime]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _verify_revenuecat_signature(body: bytes, signature: Optional[str]) -> bool:
    if not signature or not settings.revenuecat_webhook_secret:
        return False
    expected = hmac.new(
        settings.revenuecat_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
