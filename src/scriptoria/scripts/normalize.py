"""Met en forme un document déjà transcrit, sans relancer l'OCR.

Le worker met en forme chaque document à la fin de son OCR. Ce script sert aux
documents transcrits avant que la mise en forme existe, et à rejouer une mise en
forme améliorée : il ne coûte que quelques millisecondes par page, là où l'OCR en
coûte ~57 s.

Passe par `normalize_document`, comme le worker : une seule implémentation, donc
un seul comportement à tester. Rejouable : une page déjà mise en forme ou relue
par un humain est laissée telle quelle.

Lancé par `make normalize DOCUMENT=<id>`.
"""

import argparse
import asyncio
import logging
import sys
from uuid import UUID

from scriptoria.config import get_settings
from scriptoria.db.models import Document
from scriptoria.db.session import create_engine, create_sessionmaker
from scriptoria.domain.enums import DocumentStatus
from scriptoria.services.normalization import normalize_document

# Un document encore en cours d'OCR recevrait sa mise en forme deux fois : une
# ici, une autre du worker à la fin de sa dernière page.
NORMALIZABLE_STATUSES = (
    DocumentStatus.AWAITING_VALIDATION,
    DocumentStatus.VALIDATED,
    DocumentStatus.INDEXED,
)


class NormalizationRefusedError(Exception):
    """Le document n'existe pas, ou n'est pas dans un état où le mettre en forme."""


async def normalize(document_id: UUID) -> int:
    """Met en forme le document et valide la transaction. Retourne le nombre de pages."""
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_sessionmaker(engine)
    try:
        async with factory() as session:
            document = await session.get(Document, document_id)
            if document is None:
                raise NormalizationRefusedError(f"document {document_id} introuvable")
            if document.status not in NORMALIZABLE_STATUSES:
                raise NormalizationRefusedError(
                    f"document {document_id} en statut « {document.status} » : "
                    "attendre la fin de l'OCR"
                )
            count = await normalize_document(session, document_id)
            await session.commit()
    finally:
        # Refermer même en échec : la VM Docker est déjà limitée à 8 Go.
        await engine.dispose()
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Met en forme un document déjà transcrit.")
    parser.add_argument("document_id", type=UUID)
    arguments = parser.parse_args(argv)

    logging.basicConfig(level=get_settings().log_level)
    try:
        count = asyncio.run(normalize(arguments.document_id))
    # Rattrapé large : c'est un point d'entrée en ligne de commande, il doit
    # rendre un message lisible et un code de sortie, pas une trace brute.
    except Exception as exc:
        print(f"✗ mise en forme interrompue : {exc}", file=sys.stderr)
        return 1
    print(f"✓ {count} page(s) mise(s) en forme")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
