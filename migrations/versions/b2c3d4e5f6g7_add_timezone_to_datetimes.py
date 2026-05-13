"""Add timezone support to datetime columns

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2025-01-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6g7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Alter ordine_ordine.creato_il to be timezone-aware
    op.alter_column('ordine_ordine', 'creato_il',
                    existing_type=sa.DateTime(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=True)

    # Alter lotto.inizio to be timezone-aware
    op.alter_column('lotto', 'inizio',
                    existing_type=sa.DateTime(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=True)

    # Alter lotto.fine to be timezone-aware
    op.alter_column('lotto', 'fine',
                    existing_type=sa.DateTime(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=True)

    # Alter lotto.creato_il to be timezone-aware
    op.alter_column('lotto', 'creato_il',
                    existing_type=sa.DateTime(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=True)

    # Alter lotto.completato_at to be timezone-aware
    op.alter_column('lotto', 'completato_at',
                    existing_type=sa.DateTime(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=True)


def downgrade():
    # Revert lotto.completato_at to timezone-naive
    op.alter_column('lotto', 'completato_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.DateTime(),
                    existing_nullable=True)

    # Revert lotto.creato_il to timezone-naive
    op.alter_column('lotto', 'creato_il',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.DateTime(),
                    existing_nullable=True)

    # Revert lotto.fine to timezone-naive
    op.alter_column('lotto', 'fine',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.DateTime(),
                    existing_nullable=True)

    # Revert lotto.inizio to timezone-naive
    op.alter_column('lotto', 'inizio',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.DateTime(),
                    existing_nullable=True)

    # Revert ordine_ordine.creato_il to timezone-naive
    op.alter_column('ordine_ordine', 'creato_il',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.DateTime(),
                    existing_nullable=True)
