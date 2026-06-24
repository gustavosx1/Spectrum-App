# 🏛️ Arquitetura e Decisões Técnicas

Documentação de decisões arquiteturais, trade-offs e justificativas para as escolhas técnicas do Spectrum Scraper.

---

## 📐 Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                    COLETA (Sincronamente)                   │
│                     run_scraper.py (CLI)                    │
│                     orchestrator.py                         │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        ↓                         ↓
   RSS Feed                  Playwright
   (feedparser)             (fallback HTTP)
        │                         │
        └────────────┬────────────┘
                     ↓
        ┌────────────────────────┐
        │   RawArticle (Pydantic)│
        │   Deduplicação + Sort  │
        └────────────┬───────────┘
                     │
                     ↓
        ┌────────────────────────┐
        │  Enfileira no Redis    │
        │  (Celery task queue)   │
        └────────────┬───────────┘
                     │
        ┌────────────▼───────────┐
        │ PROCESSAMENTO (Async)  │
        │  worker/tasks/embed.py │
        └────────────┬───────────┘
                     │
        ┌────────────┴────────────┐
        ↓                         ↓
   Gemini API              DB Inserts
   (Embeddings)           (Supabase)
        │                         │
        └────────────┬────────────┘
                     ↓
        ┌────────────────────────┐
        │  Supabase DB           │
        │  (PostgreSQL)          │
        │  Triggers, Indices     │
        └────────────────────────┘
```

---

## 🎯 Decisões Arquiteturais

### 1. RSS + Playwright (Dupla Estratégia)

**Decisão:** Coletar via RSS primeiro; fallback para Playwright se vazio

**Justificativas:**
- ✅ RSS é rápido, confiável, respeita rate limits
- ✅ Muitos jornais brasileiros têm RSS
- ✅ Playwright para sites sem RSS (dinâmicos)
- ✅ HTTP fallback se Playwright não disponível

**Alternativas Consideradas:**
- ❌ Só RSS: perderia sites dinâmicos
- ❌ Só Playwright: muito lento, muita carga
- ❌ APIs próprias: não existem para todos

**Trade-offs:**
- Mais código para manter
- Comportamento pode ser imprevisível
- **Benefício:** Máxima cobertura de outlets

---

### 2. Async/Await para Coleta

**Decisão:** Usar `asyncio` para parallelismo de I/O

```python
# Coleta 5 outlets em paralelo
async def _collect_one(outlet, client, semaphore):
    async with semaphore:
        # Paralelismo controlado
```

**Justificativas:**
- ✅ Coleta 5x mais rápida (5 outlets simultâneos)
- ✅ Usa uma thread (sem GIL)
- ✅ Ideal para I/O-bound (HTTP requests)
- ✅ Python nativo, sem dependências extra

**Alternativas:**
- ❌ Threading: complexo, GIL problems
- ❌ Multiprocessing: overkill, mais overhead
- ❌ Sync iterativo: lento demais (5x mais tempo)

**Trade-offs:**
- Curva de aprendizado maior
- Debugging mais difícil
- **Benefício:** Performance + simplicity

---

### 3. Celery + Redis para Processamento

**Decisão:** Queue assíncrona com Celery/Redis

```python
# Run scraper (coleta, 2-5 segundos)
python3 run_scraper.py

# Worker Celery processa (15+ segundos por artigo)
celery -A worker.celery_app worker
```

**Justificativas:**
- ✅ Desacopla coleta de processamento
- ✅ Coleta é rápida (não espera Gemini/DB)
- ✅ Processamento pode ser escalado (múltiplos workers)
- ✅ Retry automático em caso de falha
- ✅ Redis fornece persistência mínima

**Alternativas:**
- ❌ Sincronamente: coleta demoraria 5+ minutos
- ❌ ThreadPoolExecutor: sem persistência, sem retry
- ❌ Bull/BullMQ: Node.js, complexo interop

**Trade-offs:**
- Complexidade extra (Redis + Celery)
- Debugging distribuído é difícil
- **Benefício:** Escalabilidade + resiliência

---

### 4. Supabase (PostgreSQL + Row Level Security)

**Decisão:** Usar Supabase em vez de MongoDB/Firebase

**Justificativas:**
- ✅ SQL para queries complexas (agrupamento, análise)
- ✅ Triggers automáticas (atualizar is_hot)
- ✅ Full-text search nativo
- ✅ Backup automático
- ✅ RLS para segurança
- ✅ Preço prevísível

**Alternativas:**
- ❌ MongoDB: sem triggers, queries complexas difíceis
- ❌ Firebase: sem controle do schema, RLS limitado
- ❌ Auto-hosted PostgreSQL: DevOps complexo

**Trade-offs:**
- Vendor lock-in Supabase
- Menos escalável que NoSQL para writes massivos
- **Benefício:** Features ricas + segurança

---

### 5. Pydantic para Validação de Dados

**Decisão:** Usar Pydantic v2 para modelos

```python
class RawArticle(BaseModel):
    url: str
    title: str
    # ... with validation
```

**Justificativas:**
- ✅ Validação automática (type hints)
- ✅ Serialização JSON/dict fácil
- ✅ Documentação integrada (schema)
- ✅ Integração com FastAPI (futuro)

**Alternativas:**
- ❌ dataclasses: sem validação
- ❌ Python puro: sem schema
- ❌ SQLAlchemy ORM: overhead extra

**Trade-offs:**
- Dependência extra
- Slight performance overhead
- **Benefício:** Type safety + DX

---

### 6. Gemini para Embeddings

**Decisão:** Usar Gemini API ao invés de embeddings locais

```python
await generate_embedding("texto aqui")  # → [0.1, 0.2, ...]
```

**Justificativas:**
- ✅ Qualidade superior (treinado em 1T+ tokens)
- ✅ Sem GPU necessário
- ✅ Suporta contexto lungo (até 2M tokens)
- ✅ Multilíngue (português OK)

**Alternativas:**
- ❌ sentence-transformers: menor qualidade
- ❌ OpenAI embeddings: 20x mais caro
- ❌ Embeddings locais: requer GPU

**Trade-offs:**
- Dependência externa (API)
- Rate limits (1500 req/min free)
- Latência de rede (100ms+)
- **Benefício:** Melhor qualidade

---

### 7. Janela de Coleta (75 minutos)

**Decisão:** Coletar apenas artigos dos últimos 75 minutos

```python
COLLECTION_WINDOW_MINUTES = 75
```

**Justificativas:**
- ✅ Evita artigos antigos
- ✅ Reduz volume de dados
- ✅ Focus em notícias fresh
- ✅ ~360 artigos/dia estimado

**Alternativas:**
- 24 horas: 3000+ artigos/dia (caro)
- 30 minutos: pode perder artigos
- Sem limite: crescimento infinito

**Trade-offs:**
- Pode perder artigos slow-published
- **Benefício:** Volume + freshness

---

### 8. Semáforo de Concorrência (5 outlets)

**Decisão:** Máximo 5 outlets coletando simultaneamente

```python
MAX_CONCURRENT_OUTLETS = 5
semaphore = asyncio.Semaphore(5)
```

**Justificativas:**
- ✅ Respeita rate limits dos sites
- ✅ Usa ~5MB memória
- ✅ Coleta ~50 artigos em 30 segundos
- ✅ Evita bans por IP

**Alternativas:**
- 1: muito lento (2+ minutos)
- 20: risco de ban, 100MB memória
- Sem limite: pode derrubar sites

**Trade-offs:**
- Coleta leva ~30-60 segundos
- **Benefício:** Respeito + confiabilidade

---

### 9. Fallback Estratificado (Playwright → HTTP → Vazio)

**Decisão:** Escaleta de fallbacks:

1. RSS (rápido)
2. Playwright (dinâmico)
3. HTTP heurístico (sem JS)
4. Vazio (se tudo falhar)

**Justificativas:**
- ✅ Máximo resilience
- ✅ Graceful degradation
- ✅ Não falha nunca (pior caso: 0 artigos)

**Trade-offs:**
- Mais código complexo
- Comportamentos podem variar
- **Benefício:** Nunca erro crítico

---

## 🔄 Fluxo de Dados

### 1. Coleta (Sincronously)

```
Outlet (Supabase)
  ↓
RSS URL válido?
  ├─ SIM: feedparser fetch
  │   └─ RawArticle[] (50ms)
  │
  └─ NÃO: Playwright?
      ├─ SIM: browser open
      │   └─ DOM parse
      │   └─ RawArticle[] (2000ms)
      │
      └─ NÃO: HTTP fallback
          └─ BeautifulSoup
          └─ RawArticle[] (200ms)
```

### 2. Processamento (Asynchronously)

```
RawArticle (from queue)
  ↓
URL já existe?
  ├─ SIM: skip
  └─ NÃO: continuar
      ↓
  Gera embedding (Gemini API)
      ↓
  Busca tópico similar
      ├─ SIM: usar existente
      └─ NÃO: criar novo
      ↓
  Insere em DB
      ↓
  Trigger: atualiza topic.article_count
      ↓
  é_hot? (count >= 3 em 24h)
      ├─ SIM: marcar is_hot=true
      └─ NÃO: ok
```

---

## 🗂️ Organização de Código

```
scraper/                    # Coleta (sync)
├── models/
│   ├── outlet.py          # Definição + catálogo
│   └── article.py         # RawArticle validado
├── collectors/
│   ├── scraper_rss.py     # feedparser
│   └── scraper_playwright.py  # browser + fallback
├── utils/
│   └── text.py            # URL norm, date parse
└── orchestrator.py        # Coordenação

worker/                     # Processamento (async)
├── tasks/
│   ├── embed.py           # Main task
│   └── cluster.py         # Análise
└── utils/
    ├── db.py              # Supabase client
    └── embedding.py       # Gemini wrapper
```

**Princípios:**
- SEPARATION OF CONCERNS: coleta vs processamento
- DRY: utilitários reutilizáveis
- SINGLE RESPONSIBILITY: cada módulo tem 1 job
- TESTABILITY: tudo isolado e testável

---

## 📊 Modelos de Dados

### OutletConfig (dict no Supabase)
```sql
CREATE TABLE outlets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  political_score FLOAT,
  
  -- RSS
  rss_feeds TEXT[] DEFAULT '{}',
  
  -- Playwright
  article_link_selector TEXT,
  title_selector TEXT,
  lead_selector TEXT,
  author_selector TEXT,
  date_selector TEXT,
  url_scrape_target TEXT,
  max_articles_per_run INT DEFAULT 30,
  
  created_at TIMESTAMP DEFAULT NOW()
);
```

### Article (from RawArticle)
```sql
CREATE TABLE articles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url TEXT NOT NULL UNIQUE,
  url_hash TEXT,
  
  outlet_id TEXT REFERENCES outlets(id),
  outlet_name TEXT,
  
  title TEXT,
  lead TEXT,
  author TEXT,
  image_url TEXT,
  content TEXT,
  
  published_at TIMESTAMP,
  collected_at TIMESTAMP DEFAULT NOW(),
  
  source TEXT ('rss' | 'play'),
  status TEXT ('raw' | 'embedded' | 'clustered' | 'verified'),
  
  embedding VECTOR(768),
  topic_id UUID REFERENCES topics(id),
  
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_articles_collected_at ON articles(collected_at DESC);
CREATE INDEX idx_articles_outlet_id ON articles(outlet_id);
CREATE INDEX idx_articles_url ON articles(url);
```

### Topics (from clustering)
```sql
CREATE TABLE topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_title TEXT NOT NULL,
  description TEXT,
  
  embedding VECTOR(768),
  
  article_count INT DEFAULT 1,
  is_hot BOOLEAN DEFAULT FALSE,
  
  similarity_threshold FLOAT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Trigger: calcular is_hot
CREATE TRIGGER update_is_hot
AFTER UPDATE ON topics
FOR EACH ROW
EXECUTE FUNCTION update_topic_hot_status();
```

---

## 🔐 Segurança

### Princípios
1. **Secrets em variáveis de ambiente** (não em código)
2. **Row Level Security** (Supabase RLS)
3. **Input validation** (Pydantic)
4. **No SQL injection** (prepared statements)
5. **Rate limiting** (semáforo + delay)

### Ameaças Mitigadas
- ❌ XSS: Sem web UI
- ❌ SQL injection: Pydantic + ORM
- ❌ API leaks: Secrets Manager
- ❌ Rate limit bans: Semáforo (5 paralelo)
- ❌ Data exfiltration: RLS

---

## 🚀 Escalabilidade

### Crescimento de 10 para 10.000 outlets

| Componente | 10 | 100 | 1000 | 10K |
|-----------|----|----|------|-----|
| Coleta | 1m | 10m | 100m | 1000m |
| Redis | 100MB | 1GB | 10GB | 100GB |
| Workers | 1 | 5 | 20 | 100 |
| DB | 100MB | 1GB | 10GB | 100GB |

**Estratégia de escalamento:**
1. Adicionar workers Celery
2. Aumentar concorrência (de 5 para 10)
3. Particionar coleta por região/tempo
4. Cache de embeddings (LRU)
5. Replicar DB (read-only replicas)

---

## 🧪 Testabilidade

### Padrões
- **Mocks**: httpx.AsyncClient mockado em testes
- **Fixtures**: pytest fixtures para outlets/articles
- **Async tests**: @pytest.mark.asyncio
- **Integration**: Teste real com Supabase dev

```python
@pytest.fixture
def outlet():
    return OutletConfig(id="test", name="Test", ...)

@pytest.mark.asyncio
async def test_rss_collection(outlet, monkeypatch):
    # Mock httpx
    monkeypatch.setattr("httpx.AsyncClient.get", mock_rss_feed)
    articles = await collect_outlet_rss(outlet, client)
    assert len(articles) > 0
```

---

## 📚 Recursos

- [Async Python](https://docs.python.org/3/library/asyncio.html)
- [Celery Docs](https://docs.celeryproject.io/)
- [Pydantic v2](https://docs.pydantic.dev/latest/)
- [Supabase Docs](https://supabase.com/docs)
- [Playwright](https://playwright.dev/python/)

---

**Última atualização:** 2026-06-24
