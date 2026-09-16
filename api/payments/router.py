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

from urllib.parse import quote

from api.middleware.auth import get_user_id
from api.models.schemas import (
    PurchaseVerifyRequest,
    PurchaseVerifyResponse,
    SubscriptionStatus,
)
from api.utils.premium import (
    activate_premium,
    claim_revenuecat_webhook_event,
    claim_purchase,
    deactivate_premium,
    get_subscription,
)
from worker.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)
REVENUECAT_SUBSCRIBER_URL = "https://api.revenuecat.com/v1/subscribers"


@router.post("/verify", response_model=PurchaseVerifyResponse)
async def verify_purchase(body: PurchaseVerifyRequest, request: Request):
    """
    Verifica compra feita nas lojas e ativa o premium.
    Chamado pelo mobile logo após o usuário completar a compra.
    """
    if not settings.enable_legacy_store_receipt_verification:
        raise HTTPException(
            status_code=410,
            detail="Verificação direta de recibos desativada; use o checkout RevenueCat",
        )

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
        external_id = result.get("external_id")
        if external_id and not claim_purchase(external_id, body.platform, user_id):
            raise HTTPException(
                status_code=409,
                detail="Este recibo de compra já está vinculado a outra conta",
            )

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


@router.post("/sync", response_model=SubscriptionStatus)
async def sync_subscription(request: Request):
    """Confirma uma compra no RevenueCat sem depender do atraso do webhook."""
    if not settings.revenuecat_secret_api_key:
        raise HTTPException(
            status_code=503,
            detail="Sincronização de assinatura indisponível",
        )

    user_id = get_user_id(request)
    subscriber = await _get_revenuecat_subscriber(user_id)
    entitlement_id = settings.revenuecat_premium_entitlement_id.strip()
    entitlements = subscriber.get("entitlements", {}) if subscriber else {}
    entitlement = entitlements.get(entitlement_id) if isinstance(entitlements, dict) else None

    if not isinstance(entitlement, dict):
        deactivate_premium(user_id)
        return get_subscription(user_id)

    expires_at = _parse_revenuecat_date(entitlement.get("expires_date"))
    if expires_at and expires_at < datetime.now(tz=timezone.utc):
        deactivate_premium(user_id)
        return get_subscription(user_id)

    product_id = entitlement.get("product_identifier")
    if not isinstance(product_id, str) or not product_id:
        deactivate_premium(user_id)
        return get_subscription(user_id)

    subscriptions = subscriber.get("subscriptions", {})
    subscription = subscriptions.get(product_id) if isinstance(subscriptions, dict) else None
    if not isinstance(subscription, dict):
        raise HTTPException(status_code=503, detail="Resposta inválida da sincronização de assinatura")

    platform = _revenuecat_platform(subscription.get("store"))
    activate_premium(
        user_id=user_id,
        platform=platform,
        product_id=product_id,
        expires_at=expires_at,
        auto_renews=not bool(subscription.get("unsubscribe_detected_at")),
    )
    return get_subscription(user_id)


@router.post("/webhook")
async def payment_webhook(
    request: Request,
    x_revenuecat_webhook_signature: str = Header(default=None),
):
    """
    Webhook do RevenueCat — notifica mudanças de assinatura (iOS + Android).

    Eventos tratados:
    - INITIAL_PURCHASE / RENEWAL / UNCANCELLATION / SUBSCRIPTION_EXTENDED → ativa premium
    - CANCELLATION → desativa apenas a renovação automática
    - EXPIRATION → desativa premium

    Cada evento é processado uma única vez pelo seu ID do RevenueCat.
    """
    body = await request.body()

    if not _verify_revenuecat_signature(body, x_revenuecat_webhook_signature):
        raise HTTPException(status_code=401, detail="Assinatura do webhook inválida")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Payload do webhook inválido") from exc

    event = payload.get("event", {})
    event_type = event.get("type")
    app_user_id = event.get("app_user_id")  # user_id do Supabase
    event_id = event.get("id")

    if not app_user_id:
        return {"status": "ignored"}

    if not _has_premium_entitlement(event):
        logger.info("payment.webhook_ignored", extra={"event_type": event_type, "reason": "entitlement"})
        return {"status": "ignored"}

    if event_type in ("INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION", "SUBSCRIPTION_EXTENDED"):
        platform = _revenuecat_platform(event.get("store"))
        external_id = event.get("original_transaction_id") or event.get("transaction_id")
        if external_id and not claim_purchase(
            external_id=str(external_id),
            platform=platform,
            user_id=app_user_id,
        ):
            logger.warning(
                "payment.webhook_ownership_conflict",
                extra={"event_id": event_id, "app_user_id": app_user_id},
            )
            return {"status": "ownership_conflict"}

        expires_at = _parse_ms(event.get("expiration_at_ms"))
        activate_premium(
            user_id=app_user_id,
            platform=platform,
            product_id=event.get("product_id", ""),
            expires_at=expires_at,
            auto_renews=True,
        )
        logger.info("Premium ativado/renovado: %s até %s", app_user_id, expires_at)

    elif event_type == "CANCELLATION":
        # Um reembolso via suporte não garante que a renovação foi desligada.
        # Preservamos o estado atual nesse caso e removemos acesso apenas em EXPIRATION.
        if event.get("cancel_reason") != "CUSTOMER_SUPPORT":
            from worker.utils.db import get_client

            get_client().table("user_profiles").update({"premium_auto_renews": False}).eq(
                "id", app_user_id
            ).execute()
            logger.info("Auto-renovação cancelada: %s", app_user_id)

    elif event_type == "EXPIRATION":
        deactivate_premium(app_user_id)
        logger.info("Premium desativado (%s): %s", event_type, app_user_id)

    # Registra a idempotência apenas depois da transição de estado. Se uma
    # chamada anterior falhar, um reenvio do RevenueCat pode concluir o fluxo.
    if event_id:
        try:
            event_timestamp_ms = int(event.get("event_timestamp_ms", 0))
        except (TypeError, ValueError):
            event_timestamp_ms = 0

        if not claim_revenuecat_webhook_event(
            event_id=str(event_id),
            event_timestamp_ms=event_timestamp_ms,
            app_user_id=app_user_id,
            event_type=event_type or "UNKNOWN",
        ):
            logger.info("payment.webhook_duplicate", extra={"event_id": event_id, "event_type": event_type})
            return {"status": "duplicate"}
    else:
        logger.warning("payment.webhook_missing_event_id", extra={"event_type": event_type})

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


async def _get_revenuecat_subscriber(user_id: str) -> Optional[dict[str, Any]]:
    """Busca o cliente no RevenueCat com uma chave secreta exclusiva do backend."""
    response = await _request_with_retries(
        "GET",
        f"{REVENUECAT_SUBSCRIBER_URL}/{quote(user_id, safe='')}",
        headers={"Authorization": f"Bearer {settings.revenuecat_secret_api_key}"},
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning("revenuecat.subscriber_lookup_failed", extra={"status_code": response.status_code})
        raise HTTPException(status_code=503, detail="Sincronização de assinatura indisponível")

    subscriber = response.json().get("subscriber")
    if not isinstance(subscriber, dict):
        raise HTTPException(status_code=503, detail="Resposta inválida da sincronização de assinatura")
    return subscriber


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

        return {
            "is_valid": True,
            "expires_at": expires_at,
            # Identificador estável da assinatura (sobrevive a renovações),
            # usado para impedir que o mesmo recibo ative premium em contas
            # diferentes — ver claim_purchase().
            "external_id": latest.get("original_transaction_id"),
        }

    return {"is_valid": False}


# ── Verificação Google ────────────────────────────────────────────────────────


async def _verify_google(purchase_token: str, product_id: str) -> dict:
    """Verifica purchase token do Android via Google Play Developer API."""
    access_token = await _google_access_token()
    if not access_token:
        return {"is_valid": False}

    url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{quote(settings.android_package_name, safe='')}/purchases/subscriptions/"
        f"{quote(product_id, safe='')}/tokens/{quote(purchase_token, safe='')}"
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

    # O próprio purchase_token já é o identificador estável da compra.
    return {"is_valid": True, "expires_at": expires_at, "external_id": purchase_token}


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


def _parse_revenuecat_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _revenuecat_platform(store: Any) -> str:
    """Converte o identificador de loja do RevenueCat ao enum interno do banco."""
    platform_map = {
        "app_store": "ios",
        "mac_app_store": "ios",
        "play_store": "android",
        "amazon": "android",
    }
    platform = platform_map.get(str(store or "").lower())
    if platform:
        return platform
    raise HTTPException(status_code=422, detail="Loja RevenueCat não suportada")


def _verify_revenuecat_signature(body: bytes, signature: Optional[str]) -> bool:
    if not signature or not settings.revenuecat_webhook_secret:
        return False

    try:
        parts = dict(part.split("=", 1) for part in signature.split(",") if "=" in part)
        timestamp = parts["t"]
        provided_signature = parts["v1"]
        if abs(time.time() - int(timestamp)) > settings.revenuecat_webhook_tolerance_seconds:
            return False
    except (KeyError, TypeError, ValueError):
        return False

    signed_payload = timestamp.encode() + b"." + body
    expected = hmac.new(
        settings.revenuecat_webhook_secret.encode(),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, provided_signature)


def _has_premium_entitlement(event: dict[str, Any]) -> bool:
    entitlement_id = settings.revenuecat_premium_entitlement_id.strip()
    entitlement_ids = event.get("entitlement_ids") or []
    return bool(entitlement_id and entitlement_id in entitlement_ids)
