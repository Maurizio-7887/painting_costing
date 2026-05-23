"""Add carico giornaliero and nesting manual history

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-05-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = 'e5f6g7h8i9j0'
down_revision = 'd4e5f6g7h8i9'
branch_labels = None
depends_on = None

def _col_exists(table, col):
    try:
        conn = op.get_bind()
        cols = [c['name'] for c in Inspector.from_engine(conn).get_columns(table)]
        return col in cols
    except Exception:
        return False

def _table_exists(table):
    try:
        conn = op.get_bind()
        return Inspector.from_engine(conn).has_table(table)
    except Exception:
        return False

def upgrade():
    # nesting_manual_history
    if not _table_exists('nesting_manual_history'):
        op.create_table('nesting_manual_history',
            sa.Column('id',         sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('ordine_id',  sa.Integer(), nullable=False),
            sa.Column('layout_json',sa.Text(),    nullable=True),
            sa.Column('kpi_json',   sa.Text(),    nullable=True),
            sa.Column('salvato_il', sa.Text(),    nullable=True),
            sa.Column('operatore',  sa.Text(),    server_default=''),
        )

    # carico_daily_log extra columns
    for col, typ in [('note', sa.Text()), ('confermato', sa.Boolean())]:
        if not _col_exists('carico_daily_log', col):
            op.add_column('carico_daily_log', sa.Column(col, typ, nullable=True))

    # poly_pts_json e svg_uri su item_master_cad
    for col, typ in [('poly_pts_json', sa.Text()), ('svg_uri', sa.Text())]:
        if not _col_exists('item_master_cad', col):
            op.add_column('item_master_cad', sa.Column(col, typ, nullable=True))

def downgrade():
    op.drop_table('nesting_manual_history')
