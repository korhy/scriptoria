"""ajoute le statut preprocessed

Un document dont les images sont nettoyées mais pas encore retranscrites a besoin
d'un état propre : le laisser en PREPROCESSING suggérerait un travail en cours,
le passer en TRANSCRIBING mentirait sur ce qui a eu lieu.

Alembic ne détecte pas l'ajout d'une valeur à un enum PostgreSQL : cette
migration est écrite à la main.

Note : SQLAlchemy stocke les *noms* des membres d'énumération Python, pas leurs
valeurs — d'où `PREPROCESSED` en majuscules et non `preprocessed`.

Revision ID: 104e7131140e
Revises: 778c7129c41e
Create Date: 2026-09-11 20:31:55.320843
"""

from collections.abc import Sequence

from alembic import op

revision: str = "104e7131140e"
down_revision: str | None = "778c7129c41e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_NAME = "document_status"
NEW_LABEL = "PREPROCESSED"

# Ordre cible du type, utilisé au retour arrière.
LABELS_WITHOUT_NEW = (
    "NEW",
    "PREPROCESSING",
    "TRANSCRIBING",
    "AWAITING_VALIDATION",
    "VALIDATED",
    "INDEXED",
    "FAILED",
)


def upgrade() -> None:
    # `IF NOT EXISTS` rend la migration rejouable sans erreur.
    op.execute(
        f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_LABEL}' BEFORE 'TRANSCRIBING'"
    )


def downgrade() -> None:
    """Retire la valeur en reconstruisant le type.

    PostgreSQL ne sait pas supprimer une valeur d'enum. Il faut recréer le type
    sans elle, ce qui impose de reclasser au préalable les documents qui la
    portent — sinon la conversion échoue. On les ramène à PREPROCESSING, l'état
    depuis lequel ils étaient arrivés.
    """
    # Littéral plutôt qu'interpolation : aucune valeur ne vient d'ailleurs que
    # de ce fichier, et l'écrire en clair évite de faire passer la requête pour
    # une construction dynamique.
    op.execute("UPDATE documents SET status = 'PREPROCESSING' WHERE status = 'PREPROCESSED'")

    labels = ", ".join(f"'{label}'" for label in LABELS_WITHOUT_NEW)
    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    op.execute(f"CREATE TYPE {ENUM_NAME} AS ENUM ({labels})")
    op.execute(
        f"ALTER TABLE documents ALTER COLUMN status TYPE {ENUM_NAME} "
        f"USING status::text::{ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {ENUM_NAME}_old")
