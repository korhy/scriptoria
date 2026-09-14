"""ajoute transcriptions.bulk_validated

Une validation groupée approuve d'un clic des pages que personne n'a forcément
lues. La révision qu'elle crée est validée — elle est indexée comme les autres —
mais doit rester reconnaissable : l'évaluation ne peut pas prendre pour référence
un texte d'OCR que nul n'a relu, sous peine d'afficher 0 % d'erreur là où
personne n'a regardé.

`server_default` à faux : toutes les révisions existantes ont été validées page
par page.

Revision ID: 5c1e0b7a9d42
Revises: 104e7131140e
Create Date: 2026-09-14 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e0b7a9d42"
down_revision: str | None = "104e7131140e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("bulk_validated", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("transcriptions", "bulk_validated")
