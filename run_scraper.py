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
from scraper.models.outlet import OUTLETS


def parse_args():
    parser = argparse.ArgumentParser(description="Spectrum Scraper")
    parser.add_argument(
        "--outlets",
        nargs="*",
        help=f"IDs dos veículos. Disponíveis: {', '.join(OUTLETS.keys())}",
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

    articles = await run_collection(outlet_ids=args.outlets)
    if args.dry_run:
        # Modo de teste — não enfileira, só imprime
        print(json.dumps(articles, ensure_ascii=False, indent=2, default=str))
        print(
            f"\n# {len(articles)} artigos coletados (dry-run, nada enfileirado)",
            file=sys.stderr,
        )
        return

    # Se o caminho de enfileiramento via Celery estiver desabilitado
    # (comentado durante desenvolvimento), mostramos um resumo mínimo
    # para que o CLI não fique silencioso.
    enqueued = 0
    for article in articles:
        enqueued += 1

    print(
        f"✓ {enqueued} artigos coletados de {len(set(a['outlet_name'] for a in articles))} veículos",
        file=sys.stderr,
    )


"""

    from worker.tasks.embed import process_article

    enqueued = 0
    for article in articles:
        process_article.delay(article)  # type: ignore[attr-defined]
        enqueued += 1

    print(
        f"✓ {enqueued} artigos enfileirados de "
        f"{len(set(a['outlet_name'] for a in articles))} veículos",
        file=sys.stderr,
    )


"""

if __name__ == "__main__":
    asyncio.run(main())

