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

### Duas configurações independentes (`Settings`)

Existem duas classes `Settings` separadas, ambas lendo o mesmo `.env`:
- `worker/config.py::settings`: API + worker (Supabase, Gemini, JWT, pagamentos, push, Redis, CORS).
- `scraper/config.py::settings`: apenas coleta (janela de tempo, timeouts Playwright, throttling).

Observação importante:
- `scraper/config.py` usa `model_config = {"extra": "allow"}`, então aceita chaves extras no `.env` sem falhar.
- Ao adicionar env var nova, confirme em qual contexto ela deve viver para evitar acoplamento acidental entre API/worker e scraper.

### Pipeline de coleta → processamento

`run_scraper.py`:
- Busca outlets no Supabase via `worker/utils/db.py::getOutlets()` (não usa o catálogo estático em runtime).
- Coleta com `scraper/orchestrator.py::run_collection(outlets)`.
- Se não for `--dry-run`, enfileira 1 task Celery por artigo em `worker.tasks.embed.process_article`.

`scraper/orchestrator.py::run_collection()`:
- Para cada outlet: tenta RSS primeiro.
- Só cai para Playwright se RSS vier vazio e houver `article_link_selector`.
- Executa outlets em paralelo com semáforo (`MAX_CONCURRENT_OUTLETS = 5`).
- Deduplica por URL e ordena por `published_at` (fallback `collected_at`).

`scraper/collectors/scraper_rss.py`:
- Extrai metadados do feed e filtra pela janela (`is_within_window`, padrão 75 min).
- Canonicaliza URL em `canonicalize_url`.
- Regra especial de roteamento por domínio:
	- quando `outlet.id == "uol_noticias"` e a URL é de `folha.uol.com.br` (inclui subdomínios como `www1.folha.uol.com.br`), grava o artigo com `outlet_id="folha_sp"` e `outlet_name="Folha de S.Paulo"`.

`worker/tasks/embed.py::_process()`:
1. Deduplica artigo por URL no banco.
2. Gera embedding (`worker/utils/embedding.py`, modelo `gemini-embedding-001`).
3. Chama RPC `find_similar_topic` para encontrar/criar tópico.
4. Insere artigo.
5. Consulta o tópico e dispara `worker.tasks.cluster.process_hot_topic.delay(topic_id)` apenas quando `article_count == hot_topic_threshold`.

`worker/tasks/cluster.py::_process()`:
- Fluxo 1 (`initial_check = false`): gera `canonical_title`, `summary` e claims para todos os artigos fundadores.
- Fluxo 2 (`initial_check = true`): gera claims apenas para novos artigos do tópico.
- Em ambos os prompts, existe instrução explícita para ignorar divergências de data de publicação e tratar como contexto de notícia recente do mesmo dia.
- Push de novo tópico usa payload versionado (`schemaVersion = "1"`) com validação pré-envio.

### API (FastAPI)

`api/main.py`:
- Registra routers: `auth`, `feed`, `notifications`, `payments`.
- `AuthMiddleware` valida JWT do Supabase:
	- HS via `SUPABASE_JWT_SECRET`.
	- ES via JWK em `SUPABASE_JWK_PUBLIC_KEY`.
	- fallback durante migração de algoritmo.
- Middleware de métricas loga `method`, `path`, `status_code`, `duration_ms` por request.
- Tratamento de erro padronizado em envelope JSON (`{"error": {...}}`).

Rotas públicas definidas em `api/middleware/auth.py`:
- `GET /health`
- `GET /docs`
- `GET /openapi.json`
- `POST /auth/refresh`
- `GET /feed/outlets`
- `GET /feed/topicsfree`
- `GET /feed/topicsfree/{topic_id}` (via prefix)
- `POST /payments/webhook`

Tier free vs premium:
- Premium é validado por `api/utils/premium.py::require_premium()` (retorna 403 se não for premium).
- Endpoints free (`/feed/topicsfree*`) retornam preview de artigos + objeto `paywall`.
- Endpoints premium (`/feed/topics*`) retornam conteúdo completo com claims.

Assinatura/pagamento:
- `POST /payments/verify` valida compra Apple/Google e ativa premium.
- `POST /payments/webhook` processa eventos RevenueCat (renovação/cancelamento/expiração/reembolso).
- `api/utils/premium.py` é a fonte de verdade para ativar/desativar premium.
- `claim_purchase` impede reutilização de recibo/token por múltiplas contas (tabela `redeemed_purchases`).

Notificações push:
- Registro de token Expo em `POST /notifications/token`.
- Desativação em `DELETE /notifications/token`.
- Validação rígida de formato do token Expo (`ExponentPushToken[...]` / `ExpoPushToken[...]`).
- Exclusão de conta em `DELETE /auth/delete` remove o usuário do Supabase Auth e limpa dados do app ligados ao usuário.

## Diretrizes detalhadas para frontend

### Objetivo de produto no app

O frontend deve enfatizar comparação editorial por espectro político, não consumo linear de manchetes:
- Tela principal centrada em tópicos (`canonical_title`, `summary`, `blindspot`).
- Detalhe do tópico organizado por colunas/seções ideológicas (`left`, `center_left`, `center`, `center_right`, `right`).
- Paywall contextual no fluxo free, sem bloquear a descoberta inicial.

### Contrato de autenticação e sessão

Fluxo recomendado:
1. Login via Supabase no cliente.
2. Enviar `Authorization: Bearer <access_token>` para rotas privadas.
3. Ao receber 401 por expiração, chamar `POST /auth/refresh` com refresh token.
4. Persistir o novo par `access_token` + `refresh_token` e repetir a requisição original.

Boas práticas:
- Implementar refresh com fila/mutex para evitar múltiplos refresh concorrentes.
- Tratar falha de refresh como logout explícito.
- Não chamar rotas premium em loop quando status de assinatura já for conhecido como inativo.

### Consumo do feed e paginação

`GET /feed/topicsfree`:
- Público.
- Limites estritos: `limit` entre 1 e 4.
- Use para home de usuário deslogado ou não premium.

`GET /feed/topics`:
- Requer premium.
- `limit` entre 1 e 50.
- Paginação por `offset`.

Recomendação de UX:
- Home: paginação incremental com prefetch de próxima página.
- Chave de cache por combinação (`route`, `limit`, `offset`, `only_hot`).
- Usar `meta.has_more` como fonte de verdade para fim da lista.

### Modelo visual de blindspot

Blindspot vem agregado por tópico:
- `left_count`
- `center_count`
- `right_count`
- `dominant_side`
- `description`

Diretrizes de UI:
- Mostrar contadores absolutos por lado (não apenas porcentagem).
- Exibir `description` quando presente como destaque editorial do card.
- Evitar “veredito normativo” no design; blindspot é indicador de cobertura, não score de qualidade.

### Detalhe do tópico (premium vs free)

Premium (`GET /feed/topics/{topic_id}`):
- Contém claims por artigo e flag `checked`.
- Deve renderizar claims com `verdict`, `confidence` e `evidence`.

Free (`GET /feed/topicsfree/{topic_id}`):
- Entrega prévia limitada por `preview_limit` (1 a 5).
- Inclui `paywall.locked_article_count` e textos de CTA.
- Não há claims no payload free.

Diretriz importante:
- Não tentar “simular” claims no frontend quando estiver no tier free.

### Push notifications e deep link

Contrato atual do push de novo tópico:
- `data.type = "NEW_TOPIC"`
- `data.topicId`
- `data.requiresPremium = "true"`
- `data.targetScreen = "TopicDetail"`
- `data.fallbackScreen = "Premium"`
- `data.deeplink = spectrum://topic/{topic_id}`
- `data.dedupKey` para deduplicação no cliente

Fluxo de navegação recomendado:
1. Recebe push.
2. Deduplica por `dedupKey`.
3. Busca status de assinatura.
4. Se premium: abre detalhe premium do tópico.
5. Se não premium: redireciona para tela de upgrade (`fallbackScreen`).

### Exclusão de conta

Fluxo recomendado:
1. Usuário confirma a ação no frontend.
2. Cliente envia `DELETE /auth/delete` com `Authorization: Bearer <access_token>`.
3. Em sucesso, o frontend limpa tokens locais, cache e estado de sessão.
4. Se a chamada falhar, manter o usuário logado e exibir a mensagem de erro.

### Erros e estados de carregamento

A API padroniza erros em envelope `error` para exceções tratadas.

No frontend:
- 401: tentar refresh de sessão; se falhar, logout.
- 403 em rota premium: mostrar paywall/contexto de assinatura.
- 404 de tópico: fallback para lista e mensagem “tópico indisponível”.
- 5xx: retry com backoff exponencial e feedback visível ao usuário.

### Datas, timezone e exibição

O backend pode receber formatos heterogêneos de data nos feeds.

No frontend:
- Exibir data/hora no timezone local do usuário.
- Priorizar contexto de recência (“há X min”) no feed.
- Evitar transformar pequenas divergências de formatação em sinal editorial.

### Instrumentação recomendada no app

Eventos mínimos para analytics de produto:
- `topic_card_impression`
- `topic_card_open`
- `paywall_view`
- `paywall_cta_click`
- `push_received`
- `push_open`
- `topic_open_from_push`

Campos úteis por evento:
- `topic_id`
- `is_premium_user`
- `blindspot_dominant_side`
- `entrypoint` (`feed`, `push`, `deeplink`)

## Pontos de atenção

- Playwright continua sendo fallback; produção opera majoritariamente em RSS.
- O catálogo `OUTLETS` em `scraper/models/outlet.py` é referência. Em runtime, a lista real vem de `outlets` no Supabase.
- Existe regra hardcoded de roteamento UOL → Folha no coletor RSS. Se surgirem mais exceções por grupo editorial, considerar mover para tabela/config no banco.
- O middleware marca `/feed/outlets` como público; isso prevalece sobre qualquer comentário de endpoint dizendo “requer autenticação”.
- `ARCHITECTURE.md` e `OPERATIONS.md` contêm runbooks de infra/deploy e devem ser consultados antes de alterar operação.
