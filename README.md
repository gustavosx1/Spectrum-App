# 📰 Spectrum Scraper

Um scraper de notícias **altamente paralelo e robusto** que coleta artigos de múltiplos veículos de comunicação brasileiros, com suporte a RSS feeds e Playwright para scraping dinâmico.

## 🎯 Características

- ✅ **Coleta Multi-Fonte**: RSS feeds + Playwright (fallback)
- ✅ **Análise Política**: Score de alinhamento político (0-100)
- ✅ **Processamento Assíncrono**: Fila Celery + Redis
- ✅ **Embedding & Clustering**: Gemini API para análise semântica
- ✅ **Deduplicação Automática**: Evita artigos repetidos via URL hash
- ✅ **Janela de Coleta**: Coleta apenas artigos recentes
- ✅ **Paralelismo Controlado**: Semáforo limita concorrência (5 outlets simultâneos)
- ✅ **Banco de Dados**: Supabase (PostgreSQL) com triggers automáticos

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                       run_scraper.py (CLI)                      │
│  Ponto de entrada — coleta artigos e enfileira no Celery       │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ↓
        ┌──────────────────────────────┐
        │  scraper/orchestrator.py     │
        │  Orquestra coleta paralela   │
        │  (semáforo 5 outlets)        │
        └──────────────┬───────────────┘
                       │
        ┌──────────────┴──────────────┐
        ↓                             ↓
   ┌────────────────┐         ┌──────────────────┐
   │  RSS Collector │         │  Playwright      │
   │  (feedparser)  │         │  (fallback HTTP) │
   └────────────────┘         └──────────────────┘
        │                             │
        └──────────────┬──────────────┘
                       ↓
            ┌──────────────────────┐
            │  Deduplicação + Sort │
            │  (por data/coleta)   │
            └──────────────────────┘
                       │
                       ↓
    ┌──────────────────────────────────────┐
    │  Celery Task: process_article.delay()│
    │  Enfileira no Redis para processamento│
    └──────────────┬───────────────────────┘
                   │
        ┌──────────┴──────────┐
        ↓                     ↓
    ┌─────────────┐     ┌──────────────┐
    │  Embedding  │     │  Clustering  │
    │  (Gemini)   │     │  (Similarity)│
    └─────────────┘     └──────────────┘
        │                     │
        └──────────────┬──────┘
                       ↓
        ┌──────────────────────────┐
        │  Supabase Database       │
        │  articles + topics       │
        │  (triggers, indices RLS) │
        └──────────────────────────┘
```

---

## 📁 Estrutura do Projeto

```
spectrum/
├── README.md                          # Este arquivo
├── requirements.txt                   # Dependências Python
├── .env.example                       # Variáveis de ambiente de exemplo
│
├── run_scraper.py                     # CLI principal
├── test_scraper.py                    # Script de diagnóstico
│
├── scraper/                           # Core de coleta
│   ├── __init__.py
│   ├── config.py                      # Configurações (timeouts, limites)
│   ├── orchestrator.py                # Orquestra coleta paralela
│   ├── models/
│   │   ├── outlet.py                  # OutletConfig + catálogo
│   │   └── article.py                 # RawArticle (Pydantic)
│   ├── collectors/
│   │   ├── scraper_rss.py             # Coleta via feedparser
│   │   └── scraper_playwright.py      # Coleta via Playwright
│   └── utils/
│       └── text.py                    # Normalização URL, parse date, etc
│
├── worker/                            # Processamento async (Celery)
│   ├── __init__.py
│   ├── celery_app.py                  # Configuração Celery
│   ├── config.py                      # Settings (API keys, thresholds)
│   ├── tasks/
│   │   ├── embed.py                   # Task: gera embedding (Gemini)
│   │   └── cluster.py                 # Task: agrupa por tópicos
│   └── utils/
│       ├── db.py                      # Cliente Supabase
│       └── embedding.py               # Geradores de embedding
│
└── tests/                             # Testes unitários
    ├── test_models_article.py
    ├── test_scraper_playwright.py
    └── test_utils_text.py
```

---

## 🚀 Quick Start

### 1. Pré-requisitos

- **Python 3.10+**
- **Redis** rodando (para Celery)
- **Supabase** account com DB criado
- **Gemini API Key** (para embeddings)

### 2. Instalação

```bash
# Clonar e navegar
git clone <repo>
cd spectrum

# Criar ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Copiar variáveis de ambiente
cp .env.example .env
# Editar .env com suas credenciais
```

### 3. Configurar `.env`

```bash
# Supabase
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua-chave-anon
# Se o Supabase emitir JWTs HS256
# SUPABASE_JWT_SECRET=seu-jwt-secret
# Para refresh token e rotas de renovação
# SUPABASE_SERVICE_ROLE_KEY=sua-service-role-key
# Se seu JWT usar JWK/ES256, cole o JSON como string
# SUPABASE_JWK_PUBLIC_KEY='{"keys":[{"kty":"EC","crv":"P-256","x":"...","y":"...","kid":"...","alg":"ES256","use":"sig"}]}'

# Gemini (para embeddings)
GEMINI_API_KEY=sua-chave-aqui
GEMINI_MODEL=gemini-1.5-flash

# Redis (para Celery)
REDIS_URL=redis://localhost:6379/0

# Thresholds
TOPIC_SIMILARITY_THRESHOLD=0.15
HOT_TOPIC_THRESHOLD=3
TOPIC_WINDOW_HOURS=24
```

### 4. Testar Configuração

```bash
# Diagnóstico completo
python3 test_scraper.py

# Esperado:
# ✅ Imports
# ✅ Supabase
# ✅ Outlets
# ✅ Models
```

### 5. Executar Scraper

```bash
# Modo dry-run (sem enfileirar)
python3 run_scraper.py --dry-run --verbose

# Modo produção (enfileira no Celery)
python3 run_scraper.py --verbose

# Outlets específicos
python3 run_scraper.py --outlets g1 folha_sp --verbose

# Lista completa de opções
python3 run_scraper.py --help
```

---

## 🛠️ Componentes Principais

### scraper/orchestrator.py
Orquestrador de coleta paralela.

**Estratégia:**
1. Para cada outlet, tenta RSS primeiro
2. Se RSS vazio OU outlet não tiver RSS, fallback para Playwright
3. Todos os outlets rodam em paralelo (semáforo 5)
4. Retorna lista deduplicada e ordenada

```python
from scraper.orchestrator import run_collection
from worker.utils.db import getOutlets

outlets = getOutlets(["g1", "folha_sp"])
articles = await run_collection(outlets)
# articles: list[dict] prontos para Celery
```

### scraper/collectors/scraper_rss.py
Coleta via RSS feeds com feedparser.

**Features:**
- Suporta múltiplos feeds por outlet
- Deduplicação automática por URL
- Filtro por janela de coleta (padrão: 75 min)
- Extração de metadados: título, lead, autor, imagem

```python
articles = await collect_outlet_rss(outlet, client)
# Retorna: list[dict] (RawArticle serializado)
```

### scraper/collectors/scraper_playwright.py
Fallback robusto: Playwright → HTTP.

**Features:**
- Usa Playwright para sites dinâmicos
- Se Playwright não instalado, fallback HTTP (BeautifulSoup)
- Extrai links via selector CSS
- Renderiza página antes de extrair
- Heurística: detecta artigos por padrão (data no path, tamanho URL)

**Configurar seletores CSS:**

```python
# Em Supabase, preencher campos:
article_link_selector     # Ex: "article h2 a" ou ".card"
title_selector            # Ex: "h1.headline"
lead_selector             # Ex: ".lead, .summary"
author_selector           # Ex: ".byline, .author"
date_selector             # Ex: "time[datetime]"
url_scrape_target         # Ex: "https://site.com/ultimas" (opt)
```

**Boas práticas para seletores:**
- Prefira seletores diretos para `<a>` quando possível
- Links relativos são resolvidos com `base_url` automaticamente
- Use `og:image` como fallback (automático)
- Teste o seletor no DevTools antes de salvar

### worker/tasks/embed.py
Task Celery: gera embedding e agrupa em tópicos.

**Fluxo:**
1. Deduplicação (verifica se URL já existe)
2. Gera embedding via Gemini
3. Busca tópico similar (threshold 0.15)
4. Cria novo tópico se não encontrar
5. Insere artigo com topic_id
6. Verifica se tópico virou "hot" (3+ artigos em 24h)

```python
# Chamado automaticamente via:
process_article.delay({"url": "...", "title": "...", ...})
```

### worker/utils/db.py
Cliente Supabase com conversão de tipos.

```python
from worker.utils.db import getOutlets, get_client

# Buscar outlets
outlets = getOutlets()           # Todos
outlets = getOutlets(["g1"])     # Específicos

# Cliente bruto para queries
db = get_client()
result = db.table("articles").select("*").execute()
```

---

## 🧩 API REST

A API expõe endpoints de autenticação, feed e pagamentos via FastAPI.

Para integração de push no app Expo, veja o guia em `FRONTEND_PUSH_GUIDE.md`.

### Atualizações para frontend (produção)

- Base URL de produção: `https://api.prismanews.com.br`
- Requisições HTTP (`http://`) retornam `301` para HTTPS.
- Em produção, `/docs` e `/openapi.json` ficam desabilitados.
- CORS aceita apenas origens definidas em `API_CORS_ORIGINS` (sem wildcard em produção).
- JWT do Supabase é validado por issuer e audience (quando configurada), com suporte a ES256/JWK.

### Rotas públicas (sem Authorization)

- `GET /health`
- `GET /feed/outlets`
- `GET /feed/topicsfree`
- `POST /auth/refresh`
- `POST /payments/webhook`

### Cabeçalhos necessários
- `Authorization: Bearer <token>`
- `Content-Type: application/json`

### Endpoints

#### GET /health
Status da API.

Response:
```json
{
  "health": "ok"
}
```

---

#### GET /auth/me
Retorna o perfil do usuário autenticado.

Response:
```json
{
  "id": "user-123",
  "email": "ana@example.com",
  "is_premium": true,
  "premium_expires_at": "2024-01-01T00:00:00",
  "created_at": "2024-01-01T00:00:00"
}
```

#### GET /auth/subscription
Retorna o status de assinatura do usuário.

Response:
```json
{
  "is_premium": true,
  "platform": "ios",
  "product_id": "monthly",
  "expires_at": "2024-01-01T00:00:00",
  "auto_renews": true
}
```

---

#### POST /auth/refresh
Renova o access token usando um refresh token do Supabase.

Request:
```json
{
  "refresh_token": "seu-refresh-token-aqui"
}
```

Response:
```json
{
  "access_token": "novo-access-token",
  "refresh_token": "novo-refresh-token",
  "expires_in": 3600,
  "token_type": "bearer"
}
```

---

#### GET /feed/topics
Lista tópicos com metadados de paginação e blindspot.

Plano pago.

Query params:
- `limit` (opcional, default: `20`, max: `50`)
- `offset` (opcional, default: `0`)
- `only_hot` (opcional, default: `false`)

Response:
```json
{
  "data": [
    {
      "id": "topic-1",
      "canonical_title": "Título do tópico",
      "summary": "Resumo",
      "article_count": 2,
      "is_hot": true,
      "initial_check": true,
      "created_at": "2024-01-02T00:00:00",
      "blindspot": {
        "left_count": 1,
        "center_count": 0,
        "right_count": 1,
        "dominant_side": null,
        "description": null
      }
    }
  ],
  "meta": {
    "limit": 20,
    "offset": 0,
    "has_more": false,
    "total": null
  }
}
```

---

#### GET /feed/outlets
Lista outlets disponíveis para filtros do frontend.

Livre para uso no frontend, sem plano pago.

Response:
```json
[
  {
    "id": "g1",
    "name": "G1",
    "political_score": 55.0
  },
  {
    "id": "folha_sp",
    "name": "Folha de S.Paulo",
    "political_score": 35.0
  }
]
```

---

#### GET /feed/topicsfree
Lista uma versão gratuita dos tópicos para o frontend.

Regras:
- Não exige plano pago.
- Retorna por padrão `3` tópicos.
- Filtra apenas tópicos `hot` por padrão.

Query params:
- `limit` (opcional, default: `3`)
- `offset` (opcional, default: `0`)
- `only_hot` (opcional, default: `true`)

Response:
```json
{
  "data": [],
  "meta": {
    "limit": 3,
    "offset": 0,
    "has_more": false,
    "total": null
  }
}
```

Cada item em `data` inclui também `image_url` quando disponível.

---

#### GET /feed/topicsfree/{topic_id}
Detalhe público capado de um tópico para usuários não autenticados.

Query params:
- `preview_limit` (opcional, default: `2`, máximo: `5`) - quantidade máxima de previews por espectro.

Response:
```json
{
  "id": "topic-1",
  "canonical_title": "Título do tópico",
  "summary": "Resumo",
  "image_url": "https://cdn.example.com/topic.jpg",
  "article_count": 8,
  "is_hot": true,
  "initial_check": true,
  "created_at": "2024-01-02T00:00:00",
  "blindspot": {
    "left_count": 3,
    "center_count": 2,
    "right_count": 3,
    "dominant_side": null,
    "description": null
  },
  "articles_left": [
    {
      "id": "art-1",
      "url": "https://a",
      "title": "A",
      "lead": "lead",
      "image_url": null,
      "author": "Ana",
      "published_at": "2024-01-03T00:00:00",
      "outlet": {
        "id": "out-1",
        "name": "Outlet A",
        "political_score": 10
      },
      "political_lean": "left"
    }
  ],
  "articles_center_left": [],
  "articles_center": [],
  "articles_center_right": [],
  "articles_right": [],
  "paywall": {
    "preview_limit": 2,
    "locked_article_count": 6,
    "cta_title": "Continue para ver todos os lados",
    "cta_description": "Assine o premium para desbloquear todos os artigos, claims e comparativos do tópico."
  }
}
```

---

#### GET /feed/topics/{topic_id}
Retorna o detalhe de um tópico, incluindo artigos agrupados por espectro político.

Response:
```json
{
  "id": "topic-1",
  "canonical_title": "Título do tópico",
  "summary": "Resumo",
  "article_count": 2,
  "is_hot": true,
  "initial_check": true,
  "created_at": "2024-01-02T00:00:00",
  "blindspot": {
    "left_count": 1,
    "center_count": 0,
    "right_count": 1,
    "dominant_side": null,
    "description": null
  },
  "articles_left": [
    {
      "id": "art-1",
      "url": "https://a",
      "title": "A",
      "lead": "lead",
      "image_url": null,
      "author": "Ana",
      "published_at": "2024-01-03T00:00:00",
      "outlet": {
        "id": "out-1",
        "name": "Outlet A",
        "political_score": 10
      },
      "political_lean": "left",
      "checked": true,
      "claims": [
        {
          "id": "claim-1",
          "claim": "Claim 1",
          "verdict": "true",
          "confidence": 0.9,
          "evidence": "evidence"
        }
      ]
    }
  ],
  "articles_center_left": [],
  "articles_center": [],
  "articles_center_right": [],
  "articles_right": [
    {
      "id": "art-2",
      "url": "https://b",
      "title": "B",
      "lead": "lead",
      "image_url": null,
      "author": "Beto",
      "published_at": "2024-01-04T00:00:00",
      "outlet": {
        "id": "out-2",
        "name": "Outlet B",
        "political_score": 90
      },
      "political_lean": "right",
      "checked": false,
      "claims": []
    }
  ]
}
```

---

#### POST /payments/verify
Verifica uma compra realizada no iOS/Android e ativa o premium.

Request:
```json
{
  "platform": "ios",
  "receipt_token": "receipttoken",
  "product_id": "monthly"
}
```

Response:
```json
{
  "is_valid": true,
  "is_premium": true,
  "expires_at": "2024-06-01T00:00:00+00:00",
  "message": "Assinatura ativada com sucesso"
}
```

---

#### GET /payments/status
Retorna o status atual da assinatura do usuário autenticado.

Response:
```json
{
  "is_premium": true,
  "platform": "ios",
  "product_id": "monthly",
  "expires_at": "2024-06-01T00:00:00+00:00",
  "auto_renews": true
}
```

---

#### POST /payments/webhook
Webhook para notificações do RevenueCat. Não requer `Authorization`.

Request headers:
- `x-revenuecat-signature`

Payload de exemplo:
```json
{
  "event": {
    "type": "INITIAL_PURCHASE",
    "app_user_id": "user-123",
    "store": "app_store",
    "product_id": "monthly",
    "expiration_at_ms": 1710000000000
  }
}
```

Response:
```json
{
  "status": "ok"
}
```

---

## ℹ️ Observações para o frontend
- Todos os endpoints exigem `Bearer token`, exceto `/health`, `/feed/outlets`, `/feed/topicsfree`, `/auth/refresh` e `/payments/webhook`.
- O frontend deve enviar `Authorization` em todas as chamadas autenticadas.
- `GET /feed/topics` continua no plano pago.
- `GET /feed/topicsfree` e `GET /feed/outlets` podem ser usados sem plano pago.
- Os dois endpoints de tópicos suportam paginação com `limit` e `offset`.
- `TopicListResponse.meta.has_more` indica se há mais páginas disponíveis.
- Em produção, use sempre `https://api.prismanews.com.br` para evitar redirect e bloqueios de mixed content.
- Se ocorrer `401` em rotas autenticadas, faça refresh de token via `POST /auth/refresh` e repita a requisição.

---

## 📌 Modelo de Erro
Erros são retornados como JSON uniforme:
```json
{
  "error": {
    "status": 404,
    "detail": "Tópico não encontrado",
    "path": "/feed/topics/unknown"
  }
}
```

### Exceção importante (auth middleware)

Algumas falhas de autenticação retornam formato simples:

```json
{
  "detail": "Token inválido ou expirado"
}
```

ou

```json
{
  "detail": "Token de autenticação ausente"
}
```

---

## 📊 Modelo de Dados

### OutletConfig
```python
@dataclass
class OutletConfig:
    id: str                               # "g1", "folha_sp", etc
    name: str                             # "G1 / Globo", "Folha de S.Paulo"
    base_url: str                         # https://g1.globo.com
    political_score: float                # 0 = esquerda, 100 = direita
    rss_feeds: list[str]                  # URLs de RSS
    article_link_selector: Optional[str]  # CSS selector para links
    title_selector: Optional[str]         # CSS selector para título
    lead_selector: Optional[str]          # CSS selector para resumo
    author_selector: Optional[str]        # CSS selector para autor
    date_selector: Optional[str]          # CSS selector para data
    url_scrape_target: Optional[str]      # URL para coleta (padrão: base_url)
    max_articles_per_run: int             # 30 (padrão)
```

### RawArticle
```python
class RawArticle(BaseModel):
    url: str                              # URL do artigo
    url_hash: str                         # SHA256 da URL (auto-gerado)
    
    outlet_id: str
    outlet_name: str
    
    title: str
    author: Optional[str]
    lead: Optional[str]                   # Resumo/descrição
    image_url: Optional[str]
    content: Optional[str]                # HTML renderizado
    
    published_at: Optional[datetime]      # Data de publicação
    collected_at: datetime                # Data de coleta
    
    source: Source                        # "rss" ou "play"
    status: ArticleStatus = "raw"
```

---

## 🔧 Configuração

### scraper/config.py
```python
class Settings:
    collection_window_minutes: int = 75        # Janela de coleta
    playwright_page_timeout_ms: int = 30000    # Timeout página
    playwright_wait_after_load_ms: int = 1500  # Espera pós-carregamento
    request_delay_seconds: float = 2.0         # Delay entre requests
    max_articles_per_run: int = 30             # Limite por outlet
```

### worker/config.py
```python
class Settings:
    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str                        # Obrigatório
    gemini_model: str = "gemini-1.5-flash"
    
    supabase_url: str                          # Obrigatório
    supabase_key: str                          # Obrigatório
    
    topic_similarity_threshold: float = 0.15   # Para agrupar tópicos
    hot_topic_threshold: int = 3               # Mínimo de artigos hot
    topic_window_hours: int = 24               # Janela hot topic
```

---

## 📡 Outlets Disponíveis

### Esquerda (0-30)
- `brasil_de_fato` (5.0): Brasil de Fato
- `the_intercept_br` (15.0): The Intercept Brasil
- `revista_forum` (8.0): Revista Fórum
- `pragmatismo_politico` (15.0): Pragmatismo Político

### Centro-Esquerda (30-45)
- `agencia_brasil` (35.0): Agência Brasil

### Centro (45-55)
- `g1` (48.0): G1 / Globo
- `uol_noticias` (45.0): UOL Notícias
- `metropoles` (50.0): Metrópoles
- `nexojornal` (35.0): Nexo Jornal

### Centro-Direita (55-70)
- `folha_sp` (62.0): Folha de S.Paulo
- `o_globo` (60.0): O Globo

### Direita (70-100)
- `o_antagonista` (78.0): O Antagonista
- `crusoe` (75.0): Crusoé
- `revista_oeste` (85.0): Revista Oeste
- `jovem_pan` (70.0): Jovem Pan
- `gazeta_do_povo` (80.0): Gazeta do Povo
- `veja` (72.0): Veja

### Adicionar Novo Outlet

Inserir em Supabase `outlets` table:
```sql
INSERT INTO outlets (
    id, name, base_url, political_score, rss_feeds, 
    article_link_selector, ...
) VALUES (
    'novo_id', 'Novo Veículo', 'https://...', 50.0,
    '["https://...feed.xml"]', 'a.article-link', ...
);
```

---

## 🎯 Casos de Uso

### Coleta RSS Simples
```bash
python3 run_scraper.py --outlets g1 folha_sp
```

### Scraping com Playwright
```bash
# Configure seletores CSS em Supabase primeiro
python3 run_scraper.py --outlets veja --verbose
```

### Análise de Tópicos Hot
```sql
SELECT * FROM topics WHERE is_hot = true 
ORDER BY created_at DESC;
```

### Monitorar Viés Político
```sql
SELECT 
    name, political_score, COUNT(*) as artigos
FROM outlets o
LEFT JOIN articles a ON o.id = a.outlet_id
WHERE a.published_at > NOW() - INTERVAL '7 days'
GROUP BY o.id, o.name, o.political_score
ORDER BY political_score;
```

---

## 🚨 Troubleshooting

### "Nenhum outlet encontrado"
```bash
python3 -c "from worker.utils.db import getOutlets; print(getOutlets())"

# Verificar:
# 1. SUPABASE_URL e SUPABASE_KEY no .env
# 2. Tabela 'outlets' existe
# 3. Permissões RLS no Supabase
```

### "Erro ao conectar Supabase"
```bash
python3 -c "
from worker.config import settings
print('URL:', settings.supabase_url)
"
```

### "RSS feed inválido"
```bash
curl https://exemplo.com/feed.xml
# Se erro: atualizar URL em Supabase
```

### "Playwright timeout"
Aumentar `playwright_page_timeout_ms` em `scraper/config.py`

### "Celery não processa"
```bash
redis-cli ping              # Verificar Redis
celery -A worker.celery_app inspect active
celery -A worker.celery_app worker --loglevel=info
```

---

## 🧪 Testes

```bash
# Diagnóstico
python3 test_scraper.py

# Unitários
pytest tests/ -v

# Integração (dry-run)
python3 run_scraper.py --dry-run --verbose
```

---

## 📦 Dependências

- `pydantic` ≥2.0.0 — Validação de dados
- `httpx` ≥0.24.0 — HTTP async
- `feedparser` ≥6.0.0 — Parse RSS
- `playwright` ≥1.40.0 — Scraping dinâmico
- `supabase` ≥2.0.0 — BD PostgreSQL
- `celery` ≥5.0.0 — Fila de tarefas
- `redis` ≥4.0.0 — Cache/fila
- `beautifulsoup4` ≥4.12.2 — Parse HTML

---

## 📄 Licença

MIT License

---

**Status:** ✅ Produção | **Última atualização:** 2026-06-24
