#!/usr/bin/env python3
"""
CLI do scraper do Spectrum.
Coleta artigos e enfileira cada um no Celery para processamento.

Flags:
    --outlets g1 folha_sp   filtra veículos (padrão: todos)
    --dry-run               coleta mas não enfileira — só imprime JSON
    --verbose               logs de debug no stderr
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

from scraper.orchestrator import run_collection
from scraper.models.outlet import OutletConfig
from worker.utils.db import getOutlets


def parse_args():
    parser = argparse.ArgumentParser(description="Spectrum Scraper")
    parser.add_argument(
        "--outlets",
        nargs="*",
        help="IDs dos veículos (padrão: todos do Supabase)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Coleta mas não enfileira — imprime JSON no stdout",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stderr,
    )

    # Busca outlets do Supabase
    try:
        outlets = getOutlets(args.outlets)
    except Exception as e:
        print(f"✗ Erro ao buscar outlets do Supabase: {e}", file=sys.stderr)
        sys.exit(1)

    if not outlets:
        print("✗ Nenhum outlet encontrado", file=sys.stderr)
        sys.exit(1)

    print(
        f"ℹ Coletando de {len(outlets)} outlet(s): {', '.join(o.name for o in outlets)}",
        file=sys.stderr,
    )

    # Coleta artigos
    articles = await run_collection(outlets)
    
    if args.dry_run:
        # Modo de teste — não enfileira, só imprime
        print(json.dumps(articles, ensure_ascii=False, indent=2, default=str))
        print(
            f"\n# {len(articles)} artigos coletados (dry-run, nada enfileirado)",
            file=sys.stderr,
        )
        return

    # Resumo da coleta
    print(
        f"✓ {len(articles)} artigos coletados de {len(set(a['outlet_name'] for a in articles)) if articles else 0} veículos",
        file=sys.stderr,
    )

    # Enfileira no Celery
    if articles:
        from worker.tasks.embed import process_article

        enqueued = 0
        for article in articles:
            try:
                process_article.delay(article)  # type: ignore[attr-defined]
                enqueued += 1
            except Exception as e:
                print(f"✗ Erro ao enfileirar artigo: {e}", file=sys.stderr)

        print(
            f"✓ {enqueued} artigos enfileirados",
            file=sys.stderr,
        )
    else:
        print("⚠ Nenhum artigo coletado", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
