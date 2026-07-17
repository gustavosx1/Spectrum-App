# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Spectrum é um agregador de notícias brasileiro com duas partes que compartilham o mesmo código-base mas rodam como processos independentes:

1. **Coleta** (`run_scraper.py` + `scraper/`) — roda periodicamente (cron/systemd), coleta artigos via RSS (Playwright é fallback, ver "Pontos de atenção") e enfileira cada artigo no Celery.
2. **Processamento** (`worker/`, Celery + Redis) — gera embeddings (Gemini), agrupa artigos em tópicos por similaridade semântica (pgvector no Supabase), e roda fact-checking com IA quando um tópico "esquenta".

A **API** (`api/`, FastAPI) expõe esses dados para o app mobile: feed de tópicos com "blindspot" (cobertura por espectro político esquerda/centro/direita), autenticação via Supabase, pagamentos (assinatura premium via App Store/Play Store + RevenueCat) e push notifications.

Todo o estado persistente vive no Supabase (Postgres + pgvector) — não há ORM nem migrations neste repositório; mudanças de schema são feitas diretamente no Supabase.

## Comandos

**Setup:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # só necessário se for usar o fallback Playwright
cp .env.example .env                    # preencher com credenciais reais
```

**Rodar a API:**
```bash
python -m uvicorn api.main:app --reload --port 8000
```

**Rodar o worker Celery** (precisa de Redis rodando):
```bash
celery -A worker.celery_app worker --loglevel=info
```

**Rodar o scraper manualmente:**
```bash
python3 run_scraper.py                       # todos os outlets, enfileira no Celery
python3 run_scraper.py --outlets g1 folha_sp  # filtra outlets específicos
python3 run_scraper.py --dry-run --verbose    # só imprime JSON, não enfileira
```

**Testes:**
```bash
pytest tests/ -v
pytest tests/test_api_endpoints.py -v
pytest tests/test_api_endpoints.py::test_profile_endpoint_returns_user_profile -v
```

Os testes não fazem chamadas de rede/DB reais: `tests/conftest.py` seta env vars dummy (`GEMINI_API_KEY`, `SUPABASE_*`) para o `pydantic-settings` não falhar no import, e `tests/test_api_endpoints.py` define um `FakeDB`/`FakeTable` em memória que imita a query builder do client Supabase (`.table().select().eq().single().execute()` etc.) via `monkeypatch`.

## Arquitetura

### Duas configurações independentes

Existem **duas classes `Settings` (pydantic-settings) separadas**, ambas lendo o mesmo `.env`:
- `worker/config.py::settings` — usada pela API e pelo worker (Supabase, Gemini, JWT, pagamentos, push).
- `scraper/config.py::settings` — usada só pelo scraper (timeouts, janela de coleta, `request_delay_seconds`). Tem `extra = "allow"`, então não falha se receber chaves desconhecidas do mesmo `.env`.

Ao adicionar uma env var nova, preste atenção em qual das duas `Settings` ela deve pertencer.

### Pipeline de coleta → processamento

`run_scraper.py` → `scraper/orchestrator.py::run_collection()`:
- Para cada outlet (catálogo em `scraper/models/outlet.py::OUTLETS`, mas a lista real vem do Supabase via `worker/utils/db.py::getOutlets()`), tenta RSS primeiro (`scraper/collectors/scraper_rss.py`).
- Playwright (`scraper/collectors/scraper_playwright.py`) só é chamado se o RSS não retornar nada **e** o outlet tiver `article_link_selector` configurado.
- Deduplicação por URL canonicalizada (`scraper/utils/text.py::canonicalize_url`) e filtro por janela de tempo (`is_within_window`, padrão 75 min).
- Cada artigo é enfileirado via `worker.tasks.embed.process_article.delay(article)`.

`worker/tasks/embed.py::_process()`:
1. Deduplica por URL no banco.
2. Gera embedding (`worker/utils/embedding.py`, modelo `gemini-embedding-001`).
3. Chama a RPC do Supabase `find_similar_topic` (busca por similaridade de embedding numa janela de horas) para achar ou criar o tópico do artigo.
4. Insere o artigo. Um **trigger no banco** atualiza `topics.article_count`/`is_hot` automaticamente (não há lógica disso no Python).
5. Se `article_count == hot_topic_threshold` (comparação exata, não `>=`, para disparar só uma vez), chama `worker.tasks.cluster.process_hot_topic.delay(topic_id)`.

`worker/tasks/cluster.py::_process()` tem dois fluxos, decididos por `topics.initial_check`:
- **Initial check** (primeira vez que o tópico fica hot): busca o HTML completo dos artigos fundadores, roda um prompt único no Gemini que gera `canonical_title` + `summary` + claims de fact-checking para todos os artigos de uma vez, marca `initial_check = true`, e dispara push notification (`_send_new_topic_push`) via Expo Push API ou webhook genérico — com um gate de validação (`_validate_push_payload`) que confere se o tópico já está publicado antes de notificar.
- **Check individual** (artigos novos chegando num tópico já inicializado): usa as claims já verificadas como contexto (em vez do conteúdo completo dos artigos anteriores) para economizar tokens, gera só as claims do artigo novo.

### API

`api/main.py` monta 4 routers (`auth`, `feed`, `notifications`, `payments`) sob um único `AuthMiddleware` (Starlette `BaseHTTPMiddleware`, em `api/middleware/auth.py`):
- Valida JWT do Supabase (HS256 via `SUPABASE_JWT_SECRET` ou ES256 via `SUPABASE_JWK_PUBLIC_KEY`, com fallback entre os dois durante migração de algoritmo).
- Libera sem token os paths em `PUBLIC_PATHS`/`PUBLIC_PATH_PREFIXES` (health, docs, refresh, endpoints do tier free, webhook de pagamento).
- Em caso de sucesso, seta `request.state.user`; `get_user_id(request)` lê o claim `sub`.

**Tier free vs premium** é resolvido pela mesma fonte de dados, não por endpoints/tabelas separadas: `api/utils/premium.py::require_premium()` levanta 403 e é chamado no início de qualquer endpoint que exige assinatura (ex.: `/feed/topics`, `/feed/topics/{id}`). Os equivalentes free (`/feed/topicsfree`, `/feed/topicsfree/{id}`) rodam a mesma query mas cortam a lista de artigos em `preview_limit` e devolvem metadados de paywall (`TopicPaywallPreview`) em vez de bloquear.

`api/utils/premium.py::activate_premium/deactivate_premium` é a **única fonte de verdade** para o estado de assinatura — tanto `/payments/verify` (chamado pelo mobile após compra) quanto `/payments/webhook` (RevenueCat, assinado via HMAC SHA-256 em `_verify_revenuecat_signature`) passam por essas funções.

## Pontos de atenção

- **Playwright nunca é usado em produção hoje** — só RSS. Há um comentário explícito em `scraper/collectors/scraper_playwright.py` confirmando isso; o código existe como fallback pronto pra quando/se for necessário, mas não foi exercitado em produção.
- `ARCHITECTURE.md` e `OPERATIONS.md` já documentam decisões técnicas e runbooks de operação/deploy em detalhe — consulte-os antes de propor mudanças de infraestrutura. `hetzner-setup.sh` é o script real usado para provisionar a VPS (systemd + nginx + cron horário do scraper), e diverge um pouco do que `OPERATIONS.md` descreve (ex.: paths `/root/spectrum` vs `/opt/spectrum`).
- O catálogo de outlets em `scraper/models/outlet.py::OUTLETS` é só um catálogo inicial/referência — em runtime, `getOutlets()` sempre lê da tabela `outlets` do Supabase.
