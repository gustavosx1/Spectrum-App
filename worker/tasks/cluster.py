from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from worker.celery_app import app
from worker.config import settings
from worker.utils.db import get_client


"""
Task cluster — disparado quando um tópico atinge o threshold (is_hot = true).

Dois fluxos distintos:
─────────────────────────────────────────────────────────────────────
Initial check (topics.initial_check = false)
    Roda uma única vez quando o tópico vira hot.
    Contexto: conteúdo completo dos N artigos fundadores.
    Produz: canonical_title, summary formatado, claims dos N artigos.
    Marca: topics.initial_check = true, articles.checked = true.

Check individual (topics.initial_check = true)
    Roda para cada artigo novo adicionado ao tópico após o initial.
    Contexto: conteúdo do artigo novo + claims já verificadas do tópico.
    Produz: claims do artigo novo (sem renomear o tópico).
    Marca: articles.checked = true.
─────────────────────────────────────────────────────────────────────
"""
logger = logging.getLogger(__name__)

PUSH_BODY_FIXED = "Venha ver todos os lados desta história"
PUSH_SCHEMA_VERSION = "1"
PUSH_TYPE_NEW_TOPIC = "NEW_TOPIC"
PUSH_TARGET_SCREEN = "TopicDetail"
PUSH_FALLBACK_SCREEN = "Premium"
EXPO_MAX_BATCH_SIZE = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/"
    f"models/{settings.gemini_model}:generateContent"
)


# ── Entry point Celery ───────────────────────────────────────────────────────


@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name="worker.tasks.cluster.process_hot_topic",
)
def process_hot_topic(self, topic_id: str) -> None:
    try:
        asyncio.run(_process(topic_id))
    except Exception as exc:
        logger.error("Falha ao processar tópico %s: %s", topic_id, exc)
        raise self.retry(exc=exc)


# ── Orquestrador ─────────────────────────────────────────────────────────────


async def _process(topic_id: str) -> None:
    db = get_client()

    topic = (
        db.table("topics")
        .select("id, initial_check")
        .eq("id", topic_id)
        .single()
        .execute()
    ).data

    if not topic:
        logger.warning("Tópico %s não encontrado", topic_id)
        return

    if not topic["initial_check"]:
        await _initial_check(db, topic_id)
    else:
        await _check_new_articles(db, topic_id)


# ── Initial check ─────────────────────────────────────────────────────────────


async def _initial_check(db, topic_id: str) -> None:
    """
    Roda uma vez. Usa o conteúdo completo dos artigos fundadores
    pra gerar título, summary e claims de todos de uma vez.
    """
    articles = _fetch_articles(db, topic_id, only_unchecked=False)

    await _fetch_contents(db, articles)

    # Re-busca com content preenchido
    articles = _fetch_articles(db, topic_id, only_unchecked=False)

    analysis = await _run_initial_prompt(articles)

    # Persiste canonical_title e summary no tópico
    db.table("topics").update(
        {
            "canonical_title": analysis["canonical_title"],
            "summary": analysis["summary"],
            "initial_check": True,
        }
    ).eq("id", topic_id).execute()

    # Persiste claims de cada artigo
    for article_result in analysis["articles"]:
        article_id = article_result["article_id"]
        _insert_claims(db, article_id, topic_id, article_result["claims"])
        db.table("articles").update({"checked": True}).eq("id", article_id).execute()

    await _send_new_topic_push(db, topic_id, analysis["canonical_title"])

    logger.info(
        "Initial check concluído — tópico %s: '%s'",
        topic_id,
        analysis["canonical_title"],
    )


# ── Check individual ──────────────────────────────────────────────────────────


async def _check_new_articles(db, topic_id: str) -> None:
    """
    Roda para artigos novos num tópico já inicializado.
    Usa as claims existentes do tópico como contexto em vez do
    conteúdo completo dos artigos anteriores — muito mais barato.
    """
    new_articles = _fetch_articles(db, topic_id, only_unchecked=True)

    if not new_articles:
        logger.info("Nenhum artigo novo pra checar no tópico %s", topic_id)
        return

    # Claims já verificadas do tópico — contexto pro LLM
    existing_claims = (
        db.table("claims")
        .select("claim, verdict, evidence")
        .eq("topic_id", topic_id)
        .execute()
    ).data

    await _fetch_contents(db, new_articles)
    new_articles = _fetch_articles(db, topic_id, only_unchecked=True)

    for article in new_articles:
        claims = await _run_individual_prompt(article, existing_claims)
        _insert_claims(db, article["id"], topic_id, claims)
        db.table("articles").update({"checked": True}).eq("id", article["id"]).execute()

        logger.info(
            "Artigo checado individualmente: %s | %d claims",
            article["url"],
            len(claims),
        )


# ── Helpers de banco ──────────────────────────────────────────────────────────


def _fetch_articles(db, topic_id: str, only_unchecked: bool) -> list[dict]:
    query = (
        db.table("articles")
        .select("id, url, title, lead, content, outlet_id")
        .eq("topic_id", topic_id)
    )
    if only_unchecked:
        query = query.eq("checked", False)
    return query.execute().data


def _insert_claims(db, article_id: str, topic_id: str, claims: list[dict]) -> None:
    if not claims:
        return
    db.table("claims").upsert(
        [
            {
                "article_id": article_id,
                "topic_id": topic_id,
                "claim": c["claim"],
                "verdict": c["verdict"],
                "confidence": c.get("confidence", 0.0),
                "evidence": c.get("evidence"),
            }
            for c in claims
        ],
        on_conflict="article_id, claim",
        ignore_duplicates=True,
    ).execute()


# ── Busca de HTML ─────────────────────────────────────────────────────────────


async def _fetch_contents(db, articles: list[dict]) -> None:
    from scraper.utils.text import html_to_text

    without = [a for a in articles if not a.get("content")]
    if not without:
        return

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        htmls = await asyncio.gather(
            *[_fetch_one(client, a["url"]) for a in without],
            return_exceptions=True,
        )

    for article, html in zip(without, htmls):
        if isinstance(html, Exception):
            logger.warning("Falha ao buscar HTML de %s: %s", article["url"], html)
            continue
        content = html_to_text(html)
        if content:
            db.table("articles").update({"content": content}).eq(
                "id", article["id"]
            ).execute()


async def _fetch_one(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, headers=HEADERS)
    resp.raise_for_status()
    return resp.text


# ── Prompts LLM ───────────────────────────────────────────────────────────────


async def _run_initial_prompt(articles: list[dict]) -> dict:
    """
    Prompt unificado do initial check.
    Uma chamada → título + summary + claims de todos os artigos.
    """
    context_parts = []
    for a in articles[:10]:
        part = f"[ID: {a['id']}]\nTítulo: {a['title']}"
        if a.get("lead"):
            part += f"\nLead: {a['lead']}"
        if a.get("content"):
            part += f"\nConteúdo: {a['content'][:800]}"
        context_parts.append(part)

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Você é um editor de notícias imparcial e fact-checker experiente.
Analise as matérias abaixo sobre o mesmo acontecimento e retorne um JSON com esta estrutura:

{{
  "canonical_title": "título neutro e objetivo em português (máx 80 caracteres)",
  "summary": "Resumo dos fatos verificáveis. [Se houver divergência entre espectros políticos, adicione:] Os espectros políticos diferem quanto a [ponto de divergência].",
  "articles": [
    {{
      "article_id": "uuid do artigo conforme indicado em [ID: ...]",
      "claims": [
        {{
          "claim": "afirmação factual verificável extraída desta matéria",
          "verdict": "true | partial | false | unverifiable",
          "confidence": 0.0,
          "evidence": "explicação do veredicto com base nas matérias e nos fatos"
        }}
      ]
    }}
  ]
}}

Regras:
- O canonical_title deve refletir os fatos confirmados pelas claims, não os títulos originais
- O summary deve começar com os fatos verificáveis e, quando possível, apontar onde os espectros divergem
- Extraia 2 a 4 claims por artigo — priorize afirmações verificáveis e divergências entre matérias
- Use "unverifiable" apenas quando não há informação suficiente nas matérias
- Retorne SOMENTE o JSON, sem markdown, sem explicação

Matérias:
{context}"""

    return await _call_gemini(prompt)


async def _run_individual_prompt(
    article: dict, existing_claims: list[dict]
) -> list[dict]:
    """
    Prompt para artigo individual pós-initial.
    Usa as claims já verificadas como contexto em vez do conteúdo
    dos artigos anteriores — economiza tokens significativamente.
    """
    claims_context = "\n".join(
        [
            f"- {c['claim']} → {c['verdict']}: {c.get('evidence', '')[:150]}"
            for c in existing_claims[:20]  # máximo 20 claims de contexto
        ]
    )

    article_text = f"Título: {article['title']}"
    if article.get("lead"):
        article_text += f"\nLead: {article['lead']}"
    if article.get("content"):
        article_text += f"\nConteúdo: {article['content'][:1200]}"

    prompt = f"""Você é um fact-checker experiente.
Analise a matéria abaixo e retorne um JSON com esta estrutura:

{{
  "claims": [
    {{
      "claim": "afirmação factual verificável extraída da matéria",
      "verdict": "true | partial | false | unverifiable",
      "confidence": 0.0,
      "evidence": "explicação do veredicto"
    }}
  ]
}}

Claims já verificadas sobre este mesmo acontecimento (use como contexto para identificar contradições):
{claims_context}

Regras:
- Extraia 2 a 4 claims da matéria
- Se uma claim contradiz algo já verificado acima, aponte isso na evidence
- Consulte também seu conhecimento sobre bases de dados oficiais (IBGE, Banco Central, TSE, Câmara)
- Retorne SOMENTE o JSON, sem markdown, sem explicação

Matéria a analisar:
{article_text}"""

    result = await _call_gemini(prompt)
    return result.get("claims", [])


async def _call_gemini(prompt: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            GEMINI_URL,
            params={"key": settings.gemini_api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1},
            },
        )
    response.raise_for_status()

    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    clean = (
        raw.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    return json.loads(clean)


def _build_new_topic_push_payload(topic_id: str, ai_title: str) -> dict:
    sent_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    dedup_key = f"topic_{topic_id}_v1"

    return {
        "notification": {
            "title": ai_title.strip(),
            "body": PUSH_BODY_FIXED,
        },
        "data": {
            "schemaVersion": PUSH_SCHEMA_VERSION,
            "type": PUSH_TYPE_NEW_TOPIC,
            "topicId": topic_id,
            "requiresPremium": "true",
            "targetScreen": PUSH_TARGET_SCREEN,
            "fallbackScreen": PUSH_FALLBACK_SCREEN,
            "deeplink": f"spectrum://topic/{topic_id}",
            "campaign": "new_topic_push",
            "sentAt": sent_at,
            "dedupKey": dedup_key,
            "locale": settings.push_locale,
            "aiTitleVersion": settings.push_ai_title_version,
        },
    }


def _is_utc_iso8601(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _validate_push_payload(db, payload: dict) -> tuple[bool, str]:
    notification = payload.get("notification") or {}
    data = payload.get("data") or {}

    topic_id = data.get("topicId", "")
    title = (notification.get("title") or "").strip()

    if not topic_id:
        return False, "topicId ausente"

    # O contrato exige topic publicado; aqui consideramos publicado
    # quando já foi processado no initial_check e está hot.
    topic = (
        db.table("topics")
        .select("id, is_hot, initial_check")
        .eq("id", topic_id)
        .single()
        .execute()
    ).data
    if not topic:
        return False, "topicId inexistente"
    if not topic.get("is_hot") or not topic.get("initial_check"):
        return False, "tópico ainda não publicado"

    if not title:
        return False, "title vazio"
    if not notification.get("body"):
        return False, "body vazio"
    if data.get("schemaVersion") != PUSH_SCHEMA_VERSION:
        return False, "schemaVersion inválido"
    if data.get("type") != PUSH_TYPE_NEW_TOPIC:
        return False, "type inválido"
    if data.get("requiresPremium") not in {"true", "false"}:
        return False, "requiresPremium inválido"
    if data.get("requiresPremium") != "true":
        return False, "requiresPremium incoerente com regra premium"
    if not data.get("targetScreen") or not data.get("fallbackScreen"):
        return False, "targetScreen/fallbackScreen ausente"
    if not data.get("sentAt") or not _is_utc_iso8601(data["sentAt"]):
        return False, "sentAt inválido"
    if not data.get("dedupKey"):
        return False, "dedupKey ausente"

    return True, "ok"


async def _dispatch_push(payload: dict) -> None:
    if not settings.push_webhook_url:
        logger.info("Push não enviado: PUSH_WEBHOOK_URL não configurado")
        return

    headers = {"Content-Type": "application/json"}
    if settings.push_webhook_bearer:
        headers["Authorization"] = f"Bearer {settings.push_webhook_bearer}"

    async with httpx.AsyncClient(timeout=settings.push_webhook_timeout_seconds) as client:
        response = await client.post(
            settings.push_webhook_url,
            json=payload,
            headers=headers,
        )
    response.raise_for_status()


def _fetch_active_push_tokens(db) -> list[str]:
    rows = (
        db.table(settings.push_device_table)
        .select(settings.push_token_column)
        .eq(settings.push_active_column, True)
        .execute()
    ).data or []

    seen: set[str] = set()
    tokens: list[str] = []
    for row in rows:
        token = (row.get(settings.push_token_column) or "").strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _build_expo_messages(tokens: list[str], payload: dict) -> list[dict]:
    notification = payload.get("notification") or {}
    data = payload.get("data") or {}
    return [
        {
            "to": token,
            "title": notification.get("title"),
            "body": notification.get("body"),
            "data": data,
            "sound": "default",
        }
        for token in tokens
    ]


def _chunk_messages(messages: list[dict], batch_size: int = EXPO_MAX_BATCH_SIZE) -> list[list[dict]]:
    return [messages[i : i + batch_size] for i in range(0, len(messages), batch_size)]


def _extract_invalid_expo_tokens(messages: list[dict], response_data: dict) -> set[str]:
    invalid: set[str] = set()
    tickets = response_data.get("data") or []
    for idx, ticket in enumerate(tickets):
        details = ticket.get("details") or {}
        if details.get("error") == "DeviceNotRegistered" and idx < len(messages):
            invalid.add(messages[idx]["to"])
    return invalid


def _mark_tokens_inactive(db, tokens: set[str]) -> None:
    if not tokens:
        return
    for token in tokens:
        (
            db.table(settings.push_device_table)
            .update({settings.push_active_column: False})
            .eq(settings.push_token_column, token)
            .execute()
        )


async def _dispatch_push_expo(db, payload: dict) -> None:
    tokens = _fetch_active_push_tokens(db)
    if not tokens:
        logger.info("Push não enviado: nenhum token Expo ativo")
        return

    messages = _build_expo_messages(tokens, payload)
    headers = {"Content-Type": "application/json"}
    if settings.push_expo_access_token:
        headers["Authorization"] = f"Bearer {settings.push_expo_access_token}"

    invalid_tokens: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.push_webhook_timeout_seconds) as client:
        for batch in _chunk_messages(messages):
            response = await client.post(
                settings.push_expo_send_url,
                json=batch,
                headers=headers,
            )
            response.raise_for_status()
            invalid_tokens.update(_extract_invalid_expo_tokens(batch, response.json()))

    _mark_tokens_inactive(db, invalid_tokens)
    if invalid_tokens:
        logger.info("Tokens Expo desativados: %d", len(invalid_tokens))


async def _send_new_topic_push(db, topic_id: str, ai_title: str) -> None:
    payload = _build_new_topic_push_payload(topic_id, ai_title)
    is_valid, reason = _validate_push_payload(db, payload)
    if not is_valid:
        logger.warning("Push cancelado para tópico %s: %s", topic_id, reason)
        return

    if settings.push_provider.lower() == "expo":
        await _dispatch_push_expo(db, payload)
    else:
        await _dispatch_push(payload)
    logger.info("Push de novo tópico enviado: %s", payload["data"]["dedupKey"])
