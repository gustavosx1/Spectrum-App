% Spectrum — Organizador de Coleta de Notícias

Visão geral
- Código escrito em Python para coletar e processar artigos via RSS e Playwright (fallback).
- Estrutura principal:
  - `scraper/` — coletors, modelos e utilitários.
  - `worker/` — tarefas assíncronas (Celery) e utils de worker.
  - `run_scraper.py` — runner simples para executar coletores.

Arquivos relevantes
- `scraper/collectors/scraper_playwright.py`: coletor por renderização (Playwright). Usa seletores configurados em `OutletConfig`.
- `scraper/models/outlet.py`: definição de `OutletConfig` e catálogo inicial.
- `scraper/models/article.py`: modelo `RawArticle` (Pydantic v2) e enums.
- `scraper/utils/text.py`: normalização de URLs, extração de lead/autor/imagem e parsing de datas.

Como configurar seletores Playwright (exemplos)
- `outlet.article_link_selector`: CSS selector que identifica os elementos que contêm o link de cada artigo.
  - Pode ser um seletor direto para o `a`, por exemplo: `article header h2 a` ou `.card a`.
  - Pode também apontar para um container que guarda o link em atributos como `data-href` ou `data-url` (ex.: `.card` quando `.card[data-href]`).
  - Exemplo: `div.feed article a.title-link` ou `div.listing .item` (se `.item` tiver `data-href`).
- `outlet.url_scrape_target`: URL do índice/página que deve ser aberta para buscar links (ex.: `https://exemplo.com/ultimas`). Se não informado, `base_url` é usado.
- `outlet.title_selector`, `lead_selector`, `author_selector`, `date_selector`, `content_selector`: seletores CSS que retornam, respectivamente, título, lead/descrição, autor, data e bloco de conteúdo. Devem apontar para um único elemento por artigo.

Boas práticas para escolher seletores
- Prefira seletores que apontem diretamente para o `a` quando possível (evita necessidade de buscar atributos).
- Se o site usa links relativos (`/noticia/123`), a resolução é feita automaticamente usando `base_url`.
- Para imagens, rely nas `og:` metas (fallback automático) ou seletores que retornem a URL da imagem.

Execução e testes
- Instale dependências: `pip install -r requirements.txt`
- Para Playwright (se usar coletor Playwright): `python -m playwright install chromium`
- Execute testes: `pytest -q`

Notas sobre deploy
- Para até ~100 usuários no primeiro ano, Docker é conveniente mas não estritamente necessário. Kubernetes é overkill inicial; discutir abaixo no arquivo de notas ou com a equipe.

----
Documentação breve criada automaticamente pelo assistente.
