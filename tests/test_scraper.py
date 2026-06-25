#!/usr/bin/env python3
"""
Script de diagnóstico do Spectrum Scraper.
Testa cada componente para identificar problemas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s | %(message)s",
)
log = logging.getLogger(__name__)


def test_imports():
    """Verifica se todos os imports funcionam."""
    log.info("🔍 Testando imports...")
    try:
        from scraper.models.outlet import OutletConfig
        log.info("✅ OutletConfig importado")
        
        from scraper.models.article import RawArticle, Source
        log.info("✅ RawArticle importado")
        
        from scraper.orchestrator import run_collection
        log.info("✅ run_collection importado")
        
        from scraper.collectors.scraper_rss import collect_outlet_rss
        log.info("✅ collect_outlet_rss importado")
        
        from worker.utils.db import getOutlets, get_client
        log.info("✅ getOutlets importado")
        
        from worker.config import settings
        log.info("✅ Settings carregadas")
        
        return True
    except Exception as e:
        log.error(f"❌ Erro ao importar: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_supabase_connection():
    """Verifica conexão com Supabase."""
    log.info("\n🔍 Testando conexão Supabase...")
    try:
        from worker.utils.db import get_client
        
        client = get_client()
        log.info("✅ Cliente Supabase criado")
        
        # Tenta fazer uma query simples
        try:
            result = client.table("outlets").select("COUNT(*)").limit(1).execute()
            log.info(f"✅ Query executada: {result.count} outlets no banco")
            return True
        except Exception as e:
            log.warning(f"⚠️  Query retornou erro (isso pode ser normal se a tabela está vazia): {e}")
            return False
            
    except Exception as e:
        log.error(f"❌ Erro ao conectar Supabase: {e}")
        log.error("   Verifique se SUPABASE_URL e SUPABASE_KEY estão no .env")
        return False


def test_outlets_loading():
    """Verifica carregamento de outlets."""
    log.info("\n🔍 Testando carregamento de outlets...")
    try:
        from worker.utils.db import getOutlets
        from scraper.models.outlet import OutletConfig
        
        outlets = getOutlets()
        log.info(f"✅ {len(outlets)} outlet(s) carregado(s) do Supabase")
        
        if outlets:
            outlet = outlets[0]
            log.info(f"\n   Outlet de exemplo: {outlet.name}")
            log.info(f"   - ID: {outlet.id}")
            log.info(f"   - Base URL: {outlet.base_url}")
            log.info(f"   - RSS feeds: {len(outlet.rss_feeds)}")
            log.info(f"   - Selector CSS: {outlet.article_link_selector or 'Nenhum'}")
            
            if not outlet.rss_feeds:
                log.warning("   ⚠️  Este outlet não tem RSS configurado!")
        else:
            log.warning("⚠️  Nenhum outlet encontrado no banco!")
        
        return len(outlets) > 0
        
    except Exception as e:
        log.error(f"❌ Erro ao carregar outlets: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_models():
    """Verifica se modelos estão funcionando."""
    log.info("\n🔍 Testando modelos...")
    try:
        from scraper.models.article import RawArticle, Source
        from datetime import datetime, UTC
        
        # Cria um artigo de teste
        article = RawArticle(
            url="https://exemplo.com/artigo",
            outlet_id="test",
            outlet_name="Test Outlet",
            title="Artigo de Teste",
            source=Source.RSS,
            published_at=datetime.now(UTC),
        )
        
        # Tenta serializar
        data = article.model_dump(mode="json")
        log.info("✅ RawArticle criado e serializado com sucesso")
        log.info(f"   - URL: {data['url']}")
        log.info(f"   - URL hash gerado: {data['url_hash'][:16]}...")
        
        return True
        
    except Exception as e:
        log.error(f"❌ Erro ao criar modelo: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Executa todos os testes."""
    log.info("=" * 60)
    log.info("🧪 SPECTRUM SCRAPER - DIAGNÓSTICO")
    log.info("=" * 60)
    
    results = {
        "Imports": test_imports(),
        "Supabase": test_supabase_connection(),
        "Outlets": test_outlets_loading(),
        "Models": test_models(),
    }
    
    log.info("\n" + "=" * 60)
    log.info("📊 RESUMO")
    log.info("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASSOU" if passed else "❌ FALHOU"
        log.info(f"{test_name:20} {status}")
    
    all_passed = all(results.values())
    
    log.info("\n" + "=" * 60)
    if all_passed:
        log.info("✅ TODOS OS TESTES PASSARAM!")
        log.info("\n🚀 Próximo passo: execute o scraper")
        log.info("   python3 run_scraper.py --dry-run --verbose")
    else:
        log.info("❌ ALGUNS TESTES FALHARAM")
        log.info("\n📝 Verificar:")
        log.info("   1. Variáveis de ambiente no .env")
        log.info("   2. Conexão com Supabase")
        log.info("   3. Tabela 'outlets' existe no banco")
        log.info("   4. Pacotes Python instalados (pip install -r requirements.txt)")
    
    log.info("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
