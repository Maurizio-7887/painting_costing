"""Add macchina_commessa and componente_macchina tables

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-05-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6g7h8'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'macchina_commessa',
        sa.Column('id',            sa.Integer(),     nullable=False),
        sa.Column('commessa',      sa.String(50),    nullable=False),
        sa.Column('num_serie',     sa.String(100),   nullable=True),
        sa.Column('nome_macchina', sa.String(200),   nullable=True),
        sa.Column('cliente',       sa.String(200),   nullable=True),
        sa.Column('colore',        sa.String(80),    nullable=True),
        sa.Column('data_consegna', sa.String(20),    nullable=True),
        sa.Column('priorita',      sa.Integer(),     nullable=True),
        sa.Column('stato',         sa.String(30),    nullable=True),
        sa.Column('doc_num',       sa.String(80),    nullable=True),
        sa.Column('slot_catena',   sa.String(20),    nullable=True),
        sa.Column('ganci_slot',    sa.Integer(),     nullable=True),
        sa.Column('operatore',     sa.String(100),   nullable=True),
        sa.Column('note',          sa.Text(),        nullable=True),
        sa.Column('piano_json',    sa.Text(),        nullable=True),
        sa.Column('creata_il',     sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('commessa'),
    )

    op.create_table(
        'componente_macchina',
        sa.Column('id',               sa.Integer(), nullable=False),
        sa.Column('macchina_id',      sa.Integer(), nullable=False),
        sa.Column('codice',           sa.String(50),  nullable=True),
        sa.Column('descrizione',      sa.String(200), nullable=True),
        sa.Column('L_mm',             sa.Float(),     nullable=True),
        sa.Column('A_mm',             sa.Float(),     nullable=True),
        sa.Column('P_mm',             sa.Float(),     nullable=True),
        sa.Column('peso_unitario',    sa.Float(),     nullable=True),
        sa.Column('ganci_pdf',        sa.Integer(),   nullable=True),
        sa.Column('qty',              sa.Integer(),   nullable=True),
        sa.Column('note',             sa.String(200), nullable=True),
        sa.Column('ganci_assegnati',  sa.Integer(),   nullable=True),
        sa.Column('posizione_gancio', sa.Integer(),   nullable=True),
        sa.ForeignKeyConstraint(['macchina_id'], ['macchina_commessa.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('componente_macchina')
    op.drop_table('macchina_commessa')
