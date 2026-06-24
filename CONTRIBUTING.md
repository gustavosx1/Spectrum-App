# 🤝 Guia de Contribuição

Agradecemos por querer contribuir para o Spectrum Scraper!

## 📋 Código de Conduta

Este projeto adota um Código de Conduta para garantir um ambiente acolhedor para todos. Leia nosso [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) antes de contribuir.

---

## 🚀 Como Começar

### 1. Fork o Projeto
```bash
# Clique em "Fork" no GitHub
# Clone seu fork local
git clone https://github.com/SEU-USERNAME/spectrum.git
cd spectrum
```

### 2. Criar Branch de Feature
```bash
# Atualize main primeiro
git fetch origin
git checkout main
git pull origin main

# Crie uma branch com nome descritivo
git checkout -b feature/seu-nome-feature
# ou
git checkout -b fix/seu-nome-fix
# ou
git checkout -b docs/seu-nome-docs
```

### 3. Fazer Alterações
```bash
# Instale ambiente de desenvolvimento
pip install -r requirements.txt
pip install -e .  # Para development

# Faça suas mudanças
# Teste localmente
pytest tests/ -v
python3 test_scraper.py
```

### 4. Commit e Push
```bash
# Commits com mensagens descritivas
git add .
git commit -m "feat: adicionar suporte para novo outlet

- Detalhe de mudança 1
- Detalhe de mudança 2

Fixes #123"

# Push para seu fork
git push origin feature/seu-nome-feature
```

### 5. Abrir Pull Request
- Vá para GitHub e clique em "New Pull Request"
- Descreva claramente o que foi mudado
- Referencie issues relacionadas (#123)
- Aguarde review

---

## 📝 Guia de Estilo

### Python
Seguimos [PEP 8](https://pep8.org/) com algumas extensões:

```python
# ✅ BOM
def normalize_outlet_name(name: str) -> str:
    """Normaliza nome do outlet removendo espaços extras.
    
    Args:
        name: Nome original do outlet
        
    Returns:
        Nome normalizado
    """
    return name.strip().lower()

# ❌ RUIM
def norm(n):
    return n.strip().lower()

# ✅ BOM - Type hints obrigatórios
from typing import Optional

def get_article(article_id: str) -> Optional[dict]:
    pass

# ❌ RUIM - Sem type hints
def get_article(article_id):
    pass
```

**Ferramentas:**
```bash
# Linting
pip install black flake8 mypy

# Verificar
black --check .
flake8 .
mypy scraper/ worker/
```

### Commits
- Use imperativo: "Add feature" (não "Added feature")
- Primeira linha ≤ 50 caracteres
- Deixe em branco a segunda linha
- Detalhe a partir da terceira linha
- Reference issues: "Fixes #123"

```
feat: adicionar suporte para X

Descrição detalhada do que foi feito.
- Mudança 1
- Mudança 2

Fixes #123
```

### Branches
```
main               # Produção
├── feature/*      # Novas features
├── fix/*          # Bug fixes
├── docs/*         # Documentação
└── refactor/*     # Refatoração
```

---

## 🧪 Testes

### Escrever Testes

```python
# tests/test_nova_feature.py
import pytest
from scraper.models.outlet import OutletConfig

def test_outlet_creation():
    """Testa criação básica de outlet."""
    outlet = OutletConfig(
        id="test",
        name="Test",
        base_url="https://test.com",
        political_score=50.0,
    )
    assert outlet.id == "test"
    assert outlet.political_score == 50.0

@pytest.mark.asyncio
async def test_collection_rss():
    """Testa coleta RSS."""
    from scraper.collectors.scraper_rss import collect_outlet_rss
    # seu teste aqui
    pass
```

### Rodando Testes

```bash
# Todos os testes
pytest tests/ -v

# Teste específico
pytest tests/test_nova_feature.py::test_outlet_creation -v

# Com coverage
pytest --cov=scraper --cov=worker tests/

# Modo watch
pytest-watch tests/
```

**Cobertura mínima:** 70%

---

## 📚 Tipos de Contribuição

### 🐛 Bug Fixes
```bash
# Template de issue
- Versão do Spectrum: X.Y.Z
- Sistema: Linux/macOS/Windows
- Python: 3.X
- Como reproduzir:
  1. ...
  2. ...
- Comportamento esperado: ...
- Comportamento atual: ...
- Logs/erros: ...
```

### ✨ Novas Features
```bash
# Template de issue
- Descrição: O que fazer e por quê
- Caso de uso: Como vai ser usado
- Implementação proposta: Como fazer
- Alternativas: Outras abordagens
```

### 📖 Documentação
- README.md: Instruções gerais
- ARCHITECTURE.md: Decisões técnicas
- OPERATIONS.md: Produção e monitoramento
- Docstrings: Em cada função

### ♻️ Refatoração
- Não muda comportamento
- Melhora performance ou legibilidade
- Inclui testes

---

## 🔍 Revisão de Código

Esperamos que todos os PRs:

- [ ] Passem nos testes (`pytest`)
- [ ] Tenham cobertura ≥ 70%
- [ ] Sigam o guia de estilo
- [ ] Tenham docstrings em funções públicas
- [ ] Tenham type hints
- [ ] Atualizem docs se necessário
- [ ] Referenciem issues relacionadas

### Checklist para Revisor
- [ ] Código é legível e bem estruturado?
- [ ] Testes cobrem os casos principais?
- [ ] Há bugs ou edge cases?
- [ ] Performance é aceitável?
- [ ] Segurança está OK?

---

## 🚀 Deployment

### Adicionar Novo Outlet
1. Criar teste em `tests/test_outlets.py`
2. Adicionar em `scraper/models/outlet.py`
3. Inserir em Supabase `outlets` table
4. Fazer PR com screenshot/evidência

### Adicionar Novo Collector
1. Criar arquivo em `scraper/collectors/scraper_novo.py`
2. Adicionar testes em `tests/test_scraper_novo.py`
3. Integrar em `orchestrator.py`
4. Documentar em README.md

---

## 📞 Dúvidas?

- 💬 Abra uma discussion no GitHub
- 🐛 Issues para bugs reportados
- 📧 Entre em contato: [email]

---

## 📄 Licença

Ao contribuir, você concorda que suas contribuições serão licenciadas sob MIT License.

---

**Obrigado por contribuir para o Spectrum!** 🙏
