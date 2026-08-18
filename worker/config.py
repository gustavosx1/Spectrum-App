from __future__ import annotations

import json
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    api_cors_origins: str = "http://localhost:3000,http://localhost:5173"
    api_allowed_hosts: str = "localhost,127.0.0.1,testserver"

    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"

    supabase_url: str
    supabase_key: str
    supabase_jwt_secret: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_jwk_public_key: Optional[str] = None
    jwt_expected_audience: Optional[str] = "authenticated"
    jwt_expected_issuer: Optional[str] = None

    # threshold para agrupar artigos sobre o mesmo tópico
    topic_similarity_threshold: float = 0.17
    hot_topic_threshold: int = 3

    topic_window_hours: int = 24
    apple_shared_secret: str = ""  # App Store Connect → In-App Purchases

    # Pagamentos — Google
    android_package_name: str = ""  # ex: com.spectrum.app
    google_service_account_json: str = ""  # JSON da service account do Play Console

    # RevenueCat (abstrai iOS + Android)
    revenuecat_webhook_secret: str = ""
    revenuecat_premium_entitlement_id: str = "premium"
    revenuecat_webhook_tolerance_seconds: int = 300
    enable_legacy_store_receipt_verification: bool = False

    # Push notifications (webhook/provider relay)
    push_provider: str = "expo"  # expo | webhook
    push_webhook_url: str = ""
    push_webhook_bearer: str = ""
    push_webhook_timeout_seconds: int = 10
    push_expo_send_url: str = "https://exp.host/--/api/v2/push/send"
    push_expo_access_token: str = ""
    push_device_table: str = "device_push_tokens"
    push_token_column: str = "expo_push_token"
    push_user_id_column: str = "user_id"
    push_active_column: str = "is_active"
    push_locale: str = "pt-BR"
    push_ai_title_version: str = "gpt-title-v2"
    push_digest_lookback_hours: int = 6

    model_config = {"env_file": ".env"}

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.api_cors_origins.split(",")]
        return [origin for origin in origins if origin]

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = [host.strip() for host in self.api_allowed_hosts.split(",")]
        return [host for host in hosts if host]

    @property
    def jwt_verification_configured(self) -> bool:
        return bool(self.supabase_jwt_secret or self.supabase_jwk_public_key)

    def jwk_configuration_error(self) -> Optional[str]:
        """Retorna erro de formato sem expor a chave pública configurada."""
        if not self.supabase_jwk_public_key:
            return None

        try:
            payload = json.loads(self.supabase_jwk_public_key)
        except (TypeError, json.JSONDecodeError):
            return "SUPABASE_JWK_PUBLIC_KEY deve conter um JSON JWKS válido"

        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list) or not keys:
            return "SUPABASE_JWK_PUBLIC_KEY deve conter ao menos uma chave em 'keys'"

        if not any(isinstance(key, dict) and key.get("kid") and key.get("kty") for key in keys):
            return "SUPABASE_JWK_PUBLIC_KEY contém uma chave pública incompleta"

        return None

    def supabase_api_key_configuration_errors(self) -> list[str]:
        """Evita caracteres invisíveis em valores enviados como HTTP headers."""
        errors: list[str] = []
        for variable_name, value in (
            ("SUPABASE_KEY", self.supabase_key),
            ("SUPABASE_SERVICE_ROLE_KEY", self.supabase_service_role_key),
        ):
            if value and any(ord(character) > 127 or character.isspace() for character in value):
                errors.append(
                    f"{variable_name} contém espaço ou caractere não ASCII; copie a chave novamente do Supabase"
                )
        return errors

    def production_configuration_errors(self) -> list[str]:
        """Retorna requisitos de segurança que não podem faltar em produção."""
        if not self.is_production:
            return []

        errors: list[str] = []
        if not self.jwt_verification_configured:
            errors.append(
                "Configure SUPABASE_JWT_SECRET (HS) ou SUPABASE_JWK_PUBLIC_KEY (ES/JWK)"
            )
        jwk_error = self.jwk_configuration_error()
        if jwk_error:
            errors.append(jwk_error)
        errors.extend(self.supabase_api_key_configuration_errors())
        if not self.supabase_service_role_key:
            errors.append("Configure SUPABASE_SERVICE_ROLE_KEY para provisionamento e exclusão de conta")
        if not self.revenuecat_webhook_secret:
            errors.append("Configure REVENUECAT_WEBHOOK_SECRET para validar eventos de assinatura")
        if not self.revenuecat_premium_entitlement_id.strip():
            errors.append("Configure REVENUECAT_PREMIUM_ENTITLEMENT_ID com o entitlement Premium do RevenueCat")
        if not self.allowed_hosts or any(host in {"*", "localhost", "127.0.0.1", "testserver"} for host in self.allowed_hosts):
            errors.append("Configure API_ALLOWED_HOSTS somente com domínios públicos da API")
        if "*" in self.cors_origins:
            errors.append("Configure API_CORS_ORIGINS com origens explícitas, sem wildcard")
        if any("localhost" in origin or "127.0.0.1" in origin for origin in self.cors_origins):
            errors.append("Remova origens localhost de API_CORS_ORIGINS em produção")
        return errors


settings = Settings()
