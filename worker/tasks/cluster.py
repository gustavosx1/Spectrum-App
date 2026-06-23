from __future__ import annotations

import asyncio
import json
import logging

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SpectrumBot/1.0; +https://spectrum.app/bot)",
    "Accept-Language": "pt-BR,pt;q=0.9",
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

