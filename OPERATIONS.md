# 🔧 Guia de Operações

Instruções para rodar, monitorar e fazer manutenção do Spectrum Scraper em produção.

---

## 🚀 Deployment

### Pré-requisitos
- Python 3.11+
- Redis 7.0+
- PostgreSQL 14+ (via Supabase)
- 2GB RAM mínimo
- 500MB disco

### Opção 1: Systemd (Linux)

**1. Criar serviço Scraper**
```bash
# /etc/systemd/system/spectrum-scraper.service
[Unit]
Description=Spectrum Scraper
After=network.target redis.service

[Service]
User=spectrum
WorkingDirectory=/opt/spectrum
ExecStart=/opt/spectrum/venv/bin/python3 run_scraper.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**2. Criar serviço Celery Worker**
```bash
# /etc/systemd/system/spectrum-worker.service
[Unit]
Description=Spectrum Celery Worker
After=network.target redis.service

[Service]
User=spectrum
WorkingDirectory=/opt/spectrum
ExecStart=/opt/spectrum/venv/bin/celery -A worker.celery_app worker \
  --loglevel=info --concurrency=4
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**3. Ativar serviços**
```bash
sudo systemctl daemon-reload
sudo systemctl enable spectrum-scraper.service
sudo systemctl enable spectrum-worker.service
sudo systemctl start spectrum-scraper.service
sudo systemctl start spectrum-worker.service
```

### Opção 2: Docker

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y \
    chromium-browser \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copiar código
COPY . .

# Rodar scraper
CMD ["python3", "run_scraper.py"]
```

**Docker Compose:**
```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data

  scraper:
    build: .
    depends_on:
      - redis
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_KEY=${SUPABASE_KEY}
      - REDIS_URL=redis://redis:6379/0
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    restart: always

  worker:
    build: .
    command: celery -A worker.celery_app worker --loglevel=info
    depends_on:
      - redis
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_KEY=${SUPABASE_KEY}
      - REDIS_URL=redis://redis:6379/0
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    restart: always

volumes:
  redis-data:
```

### Opção 3: Cron Job

```bash
# /etc/cron.d/spectrum
# Rodar a cada 6 horas
0 */6 * * * spectrum cd /opt/spectrum && /opt/spectrum/venv/bin/python3 run_scraper.py >> /var/log/spectrum.log 2>&1
```

---

## 📊 Monitoramento

### Health Checks

```bash
# Status geral
python3 -c "
from worker.utils.db import getOutlets
from worker.config import settings
import redis

# Check DB
outlets = getOutlets()
print(f'✅ Supabase: {len(outlets)} outlets')

# Check Redis
r = redis.from_url(settings.redis_url)
r.ping()
print('✅ Redis: OK')

# Check queue size
queue_size = len(r.lrange('celery', 0, -1))
print(f'📊 Queue: {queue_size} tasks')
"
```

### Logs

```bash
# Scraper logs
journalctl -u spectrum-scraper -f
journalctl -u spectrum-scraper --lines=100

# Worker logs
journalctl -u spectrum-worker -f

# Dados JSON (dry-run)
python3 run_scraper.py --dry-run 2>/dev/null | jq '.[]' | head -20
```

### Métricas no Supabase

```sql
-- Articles coletadas (últimas 24h)
SELECT COUNT(*) FROM articles 
WHERE collected_at > NOW() - INTERVAL '24 hours';

-- Distribuição por outlet
SELECT outlet_name, COUNT(*) as count
FROM articles
WHERE collected_at > NOW() - INTERVAL '24 hours'
GROUP BY outlet_name
ORDER BY count DESC;

-- Topics hot (últimas 24h)
SELECT canonical_title, article_count, is_hot
FROM topics
WHERE created_at > NOW() - INTERVAL '24 hours'
ORDER BY article_count DESC
LIMIT 10;

-- Taxa de sucesso (RSS vs Playwright)
SELECT source, COUNT(*) as count
FROM articles
WHERE collected_at > NOW() - INTERVAL '24 hours'
GROUP BY source;
```

### Alertas

```python
# Enviar alerta se nenhum artigo em 24h
import os
from worker.utils.db import get_client

db = get_client()
result = db.table("articles").select("id").eq(
    "collected_at", f">now()-interval '24 hours'"
).limit(1).execute()

if not result.data:
    # Enviar email, Slack, PagerDuty, etc
    send_alert("❌ Nenhum artigo coletado em 24h")
```

---

## 🐛 Troubleshooting

### Scraper Travado

```bash
# Verificar processo
ps aux | grep run_scraper

# Matar se necessário
pkill -f run_scraper.py

# Logs de erro
journalctl -u spectrum-scraper -n 50 --priority=err
```

### Redis Cheio

```bash
# Ver tamanho
redis-cli info memory

# Limpar fila (⚠️ CUIDADO - perderá tasks)
redis-cli FLUSHDB

# Ou limpar seletivamente
redis-cli DEL celery celery.pidbox
```

### Worker Não Processa

```bash
# Verificar se está rodando
ps aux | grep celery

# Inspect tasks
celery -A worker.celery_app inspect active

# Ver failed tasks
celery -A worker.celery_app inspect reserved

# Reiniciar
systemctl restart spectrum-worker.service
```

### Supabase Slow

```sql
-- Ver queries lentas
SELECT query, calls, mean_time, max_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;

-- Criar índice se necessário
CREATE INDEX idx_articles_collected_at ON articles(collected_at DESC);
CREATE INDEX idx_articles_outlet_id ON articles(outlet_id);
CREATE INDEX idx_topics_is_hot ON topics(is_hot);
```

---

## 🔄 Manutenção Regular

### Diário
```bash
# Verificar logs de erro
journalctl --since "24 hours ago" --priority=err

# Verificar fila Celery
celery -A worker.celery_app inspect active

# Verificar espaço em disco
df -h
```

### Semanal
```bash
# Atualizar dependências
pip list --outdated
pip install --upgrade [package]

# Limpar arquivos de debug
rm -f playwright_debug_*.html

# Backup de dados importantes
pg_dump -h host -U user -d database > backup.sql
```

### Mensal
```bash
# Rotacionar logs
logrotate /etc/logrotate.d/spectrum

# Revisar performance
# Executar testes
pytest tests/ -v

# Update feeds RSS (remover mortos)
# Testar cada feed manualmente
```

---

## 📈 Performance Tuning

### Aumentar Paralelismo

```python
# scraper/orchestrator.py
MAX_CONCURRENT_OUTLETS = 10  # De 5 para 10
```

Mas cuidado:
- Aumentar carga em sites
- Aumentar uso de memória
- Pode resultar em bans

### Aumentar Workers Celery

```bash
celery -A worker.celery_app worker \
  --concurrency=8 \  # De 4 para 8
  --prefetch-multiplier=2
```

### Cache de Embeddings

```python
# worker/utils/embedding.py
import functools

@functools.lru_cache(maxsize=1000)
async def generate_embedding(text: str):
    # Cachear últimas 1000 embeddings
    pass
```

---

## 🔐 Segurança em Produção

### Checklist

- [ ] API keys em variáveis de ambiente (não em .env)
- [ ] Redis com autenticação ativa
- [ ] Supabase RLS ativado
- [ ] HTTPS para todas conexões
- [ ] Firewall restringindo portas (6379, 5432)
- [ ] Logs não expostos publicamente
- [ ] Senhas de DB regeneradas regularmente
- [ ] Backups criptografados
- [ ] WAF (Web Application Firewall) ativado

### Backup & Disaster Recovery

```bash
# Backup automático
0 2 * * * /opt/spectrum/backup.sh

# Script backup.sh
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/backups/spectrum

# Backup Supabase
pg_dump $DATABASE_URL > $BACKUP_DIR/db_$DATE.sql.gz

# Backup Redis
redis-cli BGSAVE
cp /var/lib/redis/dump.rdb $BACKUP_DIR/redis_$DATE.rdb

# Upload para S3
aws s3 cp $BACKUP_DIR s3://seu-bucket/backups/ --recursive

# Manter últimos 30 dias
find $BACKUP_DIR -mtime +30 -delete
```

---

## 📞 Suporte

### Escalação

1. **Tiers de Alerta:**
   - INFO: Email
   - WARNING: Slack
   - ERROR: PagerDuty
   - CRITICAL: SMS

2. **On-Call Schedule:**
   - Semanas de 9 horas
   - Rotação semanal
   - Backup on-call para sábado/domingo

### Runbooks

Crie runbooks para:
- [ ] Scraper não coleta artigos
- [ ] Redis cheio
- [ ] Database lento
- [ ] API Gemini fora
- [ ] Supabase indisponível

---

## 📊 Relatórios

### Semanal
```sql
SELECT 
  DATE_TRUNC('day', collected_at) as day,
  COUNT(*) as articles,
  COUNT(DISTINCT outlet_id) as outlets,
  AVG(LENGTH(title)) as avg_title_length
FROM articles
WHERE collected_at > NOW() - INTERVAL '7 days'
GROUP BY DATE_TRUNC('day', collected_at)
ORDER BY day DESC;
```

### Mensal
```sql
SELECT 
  outlet_name,
  COUNT(*) as articles,
  COUNT(DISTINCT DATE(collected_at)) as days_active,
  COUNT(DISTINCT topic_id) as topics_created
FROM articles
WHERE collected_at > NOW() - INTERVAL '30 days'
GROUP BY outlet_name
ORDER BY articles DESC;
```

---

## 🆘 Emergency Procedures

### Rollback de Deploy

```bash
# Se novo código quebrou:
git revert HEAD
git push production
systemctl restart spectrum-scraper.service
```

### Recuperar de Falha Total

```bash
# 1. Verificar status
systemctl status spectrum-scraper
systemctl status spectrum-worker

# 2. Limpar estado corrompido
redis-cli FLUSHDB

# 3. Reiniciar
systemctl restart spectrum-scraper.service
systemctl restart spectrum-worker.service

# 4. Verificar
journalctl -u spectrum-scraper -n 20
```

---

**Última atualização:** 2026-06-24
