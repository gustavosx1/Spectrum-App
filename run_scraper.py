#!/usr/bin/env python3
"""
CLI de teste para o scraper do Spectrum.
Imprime os artigos coletados como JSON no stdout.

Exemplos:
    python run_scraper.py
    python run_scraper.py --outlets g1 folha_sp
    python run_scraper.py --outlets g1 | jq '.[0]'
    python run_scraper.py --verbose
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
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stderr,  # logs no stderr, JSON no stdout
    )

    articles = await run_collection(outlet_ids=args.outlets)
    print(json.dumps(articles, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
