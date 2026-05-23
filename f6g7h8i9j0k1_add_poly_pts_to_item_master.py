"""Add poly_pts to item_master_cad

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-05-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = 'f6g7h8i9j0k1'
down_revision = 'e5f6g7h8i9j0'
branch_labels = None
depends_on = None

def _col_exists(table, col):
    conn = op.get_bind()
    cols = [c['name'] for c in Inspector.from_engine(conn).get_columns(table)]
    return col in cols

def upgrade():
    if not _col_exists('item_master_cad', 'poly_pts_json'):
        op.add_column('item_master_cad',
            sa.Column('poly_pts_json', sa.Text(), nullable=True))
    if not _col_exists('item_master_cad', 'svg_uri'):
        op.add_column('item_master_cad',
            sa.Column('svg_uri', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('item_master_cad', 'poly_pts_json')
    op.drop_column('item_master_cad', 'svg_uri')
