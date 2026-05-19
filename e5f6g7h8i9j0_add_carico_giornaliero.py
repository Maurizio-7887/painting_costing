"""Add carico_daily_log table

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-05-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision = 'e5f6g7h8i9j0'
down_revision = 'd4e5f6g7h8i9'
branch_labels = None
depends_on = None


def _table_exists(name):
    conn = op.get_bind()
    return name in Inspector.from_engine(conn).get_table_names()


def upgrade():
    if not _table_exists('carico_daily_log'):
        op.create_table(
            'carico_daily_log',
            sa.Column('id',              sa.Integer(),    nullable=False),
            sa.Column('data',            sa.String(10),   nullable=False),
            sa.Column('ora',             sa.String(8),    nullable=False),
            sa.Column('commessa',        sa.String(80),   nullable=True),
            sa.Column('num_serie',       sa.String(100),  nullable=True),
            sa.Column('cliente',         sa.String(200),  nullable=True),
            sa.Column('colore',          sa.String(80),   nullable=True),
            sa.Column('operaio',         sa.String(100),  nullable=True),
            sa.Column('n_ganci',         sa.Integer(),    nullable=True),
            sa.Column('velocita_mmin',   sa.Float(),      nullable=True),
            sa.Column('kg_totali',       sa.Float(),      nullable=True),
            sa.Column('codici_json',     sa.Text(),       nullable=True),
            sa.Column('note',            sa.Text(),       nullable=True),
            sa.Column('costo_calcolato', sa.Float(),      nullable=True),
            sa.Column('immagine_path',   sa.String(300),  nullable=True),
            sa.Column('creato_il',       sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_carico_daily_log_data', 'carico_daily_log', ['data'])
    else:
        print("  [SKIP] carico_daily_log esiste già")


def downgrade():
    op.drop_index('ix_carico_daily_log_data', 'carico_daily_log')
    op.drop_table('carico_daily_log')
