# Spectrum

Agregador de noticias com coleta automatica, processamento semantico e API para app mobile.

Este repositorio contem tres blocos principais:
- Coleta (`run_scraper.py` + `scraper/`): busca noticias recentes por RSS (Playwright como fallback).
- Processamento (`worker/`): embeddings, agrupamento em topicos e fact-check assistido por IA.
- API (`api/`): feed de topicos, autenticacao, assinatura premium e push notifications.

Todo o estado persistente fica no Supabase (Postgres + pgvector).

## Arquitetura rapida

Fluxo principal:
1. `run_scraper.py` busca outlets no Supabase.
2. `scraper/orchestrator.py` coleta em paralelo (RSS primeiro, Playwright so se necessario).
3. Cada artigo vai para `worker.tasks.embed.process_article` (Celery + Redis).
4. O worker gera embedding, encontra/cria topico e insere artigo.
5. Quando topico atinge threshold (`article_count == hot_topic_threshold`), dispara `worker.tasks.cluster.process_hot_topic`.
6. Cluster gera titulo/resumo/claims e envia push de novo topico.

## Requisitos

- Python 3.11+
- Redis
- Projeto Supabase configurado
- Chave Gemini

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Observacao:
- Playwright e fallback. Em producao atual, o caminho principal e RSS.

## Execucao

API:
```bash
python -m uvicorn api.main:app --reload --port 8000
```

Worker Celery:
```bash
celery -A worker.celery_app worker --loglevel=info
```

Scraper manual:
```bash
python3 run_scraper.py
python3 run_scraper.py --outlets g1 folha_sp
python3 run_scraper.py --dry-run --verbose
```

## Configuracao (`.env`)

Principais variaveis usadas no codigo:

Infra:
- `REDIS_URL`
- `SUPABASE_URL`
- `SUPABASE_KEY`

JWT/Auth:
- `SUPABASE_JWT_SECRET` (HS)
- `SUPABASE_JWK_PUBLIC_KEY` (ES/JWK)
- `JWT_EXPECTED_AUDIENCE`
- `JWT_EXPECTED_ISSUER`
- `SUPABASE_SERVICE_ROLE_KEY` (provisionamento de perfil e exclusão de conta)
- `APP_ENV=production` e `API_ALLOWED_HOSTS` (obrigatórios em produção); defina `API_CORS_ORIGINS` somente para domínios de clientes web

IA:
- `GEMINI_API_KEY`
- `GEMINI_MODEL` (default no codigo: `gemini-2.5-flash`)

Agrupamento:
- `TOPIC_SIMILARITY_THRESHOLD`
- `TOPIC_WINDOW_HOURS`
- `HOT_TOPIC_THRESHOLD`

Push:
- `PUSH_PROVIDER` (`expo` ou `webhook`)
- `PUSH_WEBHOOK_URL`
- `PUSH_WEBHOOK_BEARER`
- `PUSH_EXPO_SEND_URL`
- `PUSH_EXPO_ACCESS_TOKEN`

Pagamentos:
- `REVENUECAT_WEBHOOK_SECRET`
- `REVENUECAT_PREMIUM_ENTITLEMENT_ID` (padrão: `premium`)

## Regras importantes da coleta

- Janela de recencia padrao: 75 minutos.
- Deduplicacao por URL canonicalizada.
- Outlets sao lidos da tabela `outlets` no Supabase (catalogo local e referencia, nao fonte runtime).
- Regra especial no RSS:
  - Se o outlet for `uol_noticias` e a URL for de `folha.uol.com.br` (incluindo subdominios), o artigo e salvo como `folha_sp`.

## Cluster / Fact-check

Dois modos:
- Initial check: processa os artigos fundadores do topico de uma vez.
- Check individual: processa apenas artigos novos de topicos ja inicializados.

Regra de prompt vigente:
- O modelo deve ignorar divergencias de data de publicacao entre materias e tratar o contexto como noticia recente do mesmo dia.

## API para frontend

### Rotas publicas

- `GET /health`
- `POST /auth/refresh`
- `GET /feed/outlets`
- `GET /feed/topicsfree`
- `GET /feed/topicsfree/{topic_id}`
- `POST /payments/webhook`

Observacao:
- Em ambiente nao-producao, `/docs` e `/openapi.json` podem estar habilitados.

### Rotas autenticadas

- `GET /auth/me`
- `GET /auth/subscription`
- `GET /feed/topics`
- `GET /feed/topics/{topic_id}`
- `POST /notifications/token`
- `DELETE /notifications/token`
- `DELETE /auth/delete`
- `POST /payments/verify`
- `GET /payments/status`

### Tier free vs premium

- Free:
  - `GET /feed/topicsfree` (limit 1..4, `only_hot` default true)
  - `GET /feed/topicsfree/{topic_id}` com `preview_limit` (1..5)
  - Retorna preview + objeto `paywall`
- Premium:
  - `GET /feed/topics` (limit 1..50)
  - `GET /feed/topics/{topic_id}`
  - Retorna artigos completos + claims

### Contrato de erro

Erros tratados pela API usam envelope:

```json
{
  "error": {
    "status": 404,
    "detail": "Topico nao encontrado",
    "path": "/feed/topics/abc"
  }
}
```

Falhas de autenticacao no middleware retornam:

```json
{"detail": "Token invalido ou expirado"}
```

ou

```json
{"detail": "Token de autenticacao ausente"}
```

## Diretrizes objetivas para frontend

Sessao/auth:
1. Enviar `Authorization: Bearer <access_token>` nas rotas privadas.
2. No Expo, usar o SDK Supabase como fonte única da sessão e seu refresh integrado.
3. Ao receber `401`, renovar uma vez com o SDK e repetir a requisição original.
4. Se a renovação falhar, limpar a sessão e solicitar novo login.

O endpoint `POST /auth/refresh` existe apenas para compatibilidade de clientes
externos legados; o aplicativo Spectrum não deve enviar refresh tokens por ele.

Feed e cache:
- Paginacao por `limit`/`offset`.
- Usar `meta.has_more` como sinal de fim de lista.
- Separar cache de free e premium.

Blindspot:
- Exibir contadores absolutos (`left_count`, `center_count`, `right_count`).
- Mostrar `description` quando existir.

Paywall:
- Em detalhe free, usar `paywall.locked_article_count` e CTAs do backend.
- Nao renderizar claims no tier free.

Push/deeplink:
- Payload `NEW_TOPIC` inclui `topicId`, `requiresPremium`, `targetScreen`, `fallbackScreen`, `deeplink` e `dedupKey`.
- Deduplicar push por `dedupKey` no cliente.

Delete de conta:
- Chamar `DELETE /auth/delete` com `Authorization: Bearer <access_token>`.
- Depois de sucesso, limpar tokens locais, estado de sessão e caches ligados ao usuário.
- O backend remove o usuário do Supabase Auth e limpa dados do app ligados a `user_profiles`, `device_push_tokens` e `redeemed_purchases`.

## Testes

Suite completa:
```bash
pytest tests/ -v
```

Foco API:
```bash
pytest tests/test_api_endpoints.py -v
```

Foco cluster/push:
```bash
pytest tests/test_cluster.py -v
```

Os testes usam dublês (fake DB/client) e nao dependem de rede/banco reais.

## Troubleshooting rapido

Validar outlets carregados do Supabase:
```bash
python3 -c "from worker.utils.db import getOutlets; print(len(getOutlets()))"
```

Rodar coleta sem enfileirar:
```bash
python3 run_scraper.py --dry-run --verbose
```

Verificar Redis:
```bash
redis-cli ping
```

## Documentacao complementar

- `CLAUDE.md`: contexto tecnico consolidado do repositorio.
- `ARCHITECTURE.md`: decisoes e desenho da solucao.
- `OPERATIONS.md`: runbook operacional/deploy.
