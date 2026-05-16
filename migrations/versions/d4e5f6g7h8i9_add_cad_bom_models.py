"""add_cad_bom_models

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-05-16 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6g7h8i9'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'item_master_cad',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codice_art', sa.String(20), nullable=False),
        sa.Column('nome', sa.String(200), nullable=False),
        sa.Column('assembly_file', sa.String(200), nullable=True),
        sa.Column('superficie_m2', sa.Float(), nullable=True),
        sa.Column('volume_m3', sa.Float(), nullable=True),
        sa.Column('peso_kg', sa.Float(), nullable=True),
        sa.Column('lunghezza_mm', sa.Float(), nullable=True),
        sa.Column('larghezza_mm', sa.Float(), nullable=True),
        sa.Column('altezza_mm', sa.Float(), nullable=True),
        sa.Column('cog_x_mm', sa.Float(), nullable=True),
        sa.Column('cog_y_mm', sa.Float(), nullable=True),
        sa.Column('cog_z_mm', sa.Float(), nullable=True),
        sa.Column('passo_gancio_m', sa.Float(), nullable=True),
        sa.Column('complessita', sa.Integer(), nullable=True),
        sa.Column('hash_geom', sa.String(20), nullable=True),
        sa.Column('mesh_presente', sa.Boolean(), nullable=True),
        sa.Column('creato_il', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codice_art'),
    )

    op.create_table(
        'bom_assembly',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(200), nullable=False),
        sa.Column('file_step', sa.String(300), nullable=True),
        sa.Column('n_parti_uniche', sa.Integer(), nullable=True),
        sa.Column('n_parti_totali', sa.Integer(), nullable=True),
        sa.Column('peso_totale_kg', sa.Float(), nullable=True),
        sa.Column('sup_totale_m2', sa.Float(), nullable=True),
        sa.Column('parse_json', sa.Text(), nullable=True),
        sa.Column('creato_il', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'bom_record_cad',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assembly_id', sa.Integer(), nullable=False),
        sa.Column('codice_art', sa.String(20), nullable=True),
        sa.Column('nome_part', sa.String(200), nullable=True),
        sa.Column('livello', sa.Integer(), nullable=True),
        sa.Column('nome_parent', sa.String(200), nullable=True),
        sa.Column('qty', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['assembly_id'], ['bom_assembly.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ordine_cad',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assembly_id', sa.Integer(), nullable=True),
        sa.Column('n_unita', sa.Integer(), nullable=True),
        sa.Column('stato', sa.String(30), nullable=True),
        sa.Column('nesting_json', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('creato_il', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['assembly_id'], ['bom_assembly.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('ordine_cad')
    op.drop_table('bom_record_cad')
    op.drop_table('bom_assembly')
    op.drop_table('item_master_cad')
