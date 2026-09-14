"""ajoute l'origine de révision normalized

La mise en forme automatique d'une sortie OCR (césures recollées, lignes remises
en paragraphes, numéros de page retirés) crée sa propre révision : la révision
`ocr` reste la lecture brute du modèle, et c'est elle qu'on mesure.

Alembic ne détecte pas l'ajout d'une valeur à un enum PostgreSQL : cette
migration est écrite à la main. SQLAlchemy stocke les *noms* des membres
d'énumération, d'où `NORMALIZED` en majuscules.

Revision ID: 9b3f6d2e8a14
Revises: 5c1e0b7a9d42
Create Date: 2026-09-14 15:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9b3f6d2e8a14"
down_revision: str | None = "5c1e0b7a9d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_NAME = "transcription_origin"
NEW_LABEL = "NORMALIZED"

# Ordre cible du type, utilisé au retour arrière.
LABELS_WITHOUT_NEW = ("OCR", "HUMAN")


def upgrade() -> None:
    # `IF NOT EXISTS` rend la migration rejouable sans erreur.
    op.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_LABEL}'")


def downgrade() -> None:
    """Retire la valeur en reconstruisant le type.

    Les révisions mises en forme sont **supprimées**, pas reclassées : les passer
    en `OCR` ferait prendre une mise en forme pour la lecture du modèle. Elles se
    reconstruisent depuis les révisions `ocr` par `make normalize`, et leurs blocs
    de confiance partent avec elles (clé étrangère en cascade). Une relecture
    humaine enregistrée par-dessus reste, avec un trou dans la numérotation.
    """
    op.execute("DELETE FROM transcriptions WHERE origin = 'NORMALIZED'")

    labels = ", ".join(f"'{label}'" for label in LABELS_WITHOUT_NEW)
    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    op.execute(f"CREATE TYPE {ENUM_NAME} AS ENUM ({labels})")
    op.execute(
        f"ALTER TABLE transcriptions ALTER COLUMN origin TYPE {ENUM_NAME} "
        f"USING origin::text::{ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {ENUM_NAME}_old")
