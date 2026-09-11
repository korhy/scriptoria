"""Reconstruit intégralement l'index Elasticsearch depuis Postgres.

Ce script est la garantie opérationnelle qu'Elasticsearch reste jetable. Tant
qu'il fonctionne, perdre le volume ES n'est pas un incident : on rejoue.

Lancé par `make reindex`.
"""

import asyncio
import sys


async def reindex_all() -> int:
    """Réindexe toutes les transcriptions validées. Retourne le nombre de fragments."""
    raise NotImplementedError(
        "Réindexation non implémentée : nécessite services/chunking.py, "
        "services/embeddings.py et services/indexing.py."
    )


def main() -> int:
    try:
        count = asyncio.run(reindex_all())
    except NotImplementedError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ {count} fragments réindexés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
