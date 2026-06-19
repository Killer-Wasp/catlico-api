"""add case templates and tags

Revision ID: b8d1f4a6c2e3
Revises: a7c9e2f5b3d4
Create Date: 2026-06-13 14:45:00.000000

Case templates (scalar defaults + task scaffolding) and a global tag vocabulary with
polymorphic tagging over case/observable/alert/case_template. Absent tag namespace/value
are stored as '' (not NULL) so the unique constraint is airtight.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8d1f4a6c2e3'
down_revision: Union[str, Sequence[str], None] = 'a7c9e2f5b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'tag',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('namespace', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('predicate', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('colour', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('namespace', 'predicate', 'value', name='uq_tag_identity'),
    )

    op.create_table(
        'tagging',
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.Column(
            'taggable_type',
            sa.Enum('case', 'observable', 'alert', 'case_template', name='taggabletype'),
            nullable=False,
        ),
        sa.Column('taggable_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['tag_id'], ['tag.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('tag_id', 'taggable_type', 'taggable_id'),
    )
    op.create_index(
        op.f('ix_tagging_taggable_id'), 'tagging', ['taggable_id'], unique=False
    )

    op.create_table(
        'case_template',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title_prefix', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=True),
        sa.Column('tlp', sa.Integer(), nullable=True),
        sa.Column('pap', sa.Integer(), nullable=True),
        sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_case_template_name'), 'case_template', ['name'], unique=False)
    op.create_index(
        op.f('ix_case_template_organisation_id'),
        'case_template',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_case_template_deleted_at'), 'case_template', ['deleted_at'], unique=False
    )

    op.create_table(
        'case_template_task',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('group', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['template_id'], ['case_template.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_case_template_task_template_id'),
        'case_template_task',
        ['template_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('case_template_task')
    op.drop_table('case_template')
    op.drop_index(op.f('ix_tagging_taggable_id'), table_name='tagging')
    op.drop_table('tagging')
    op.drop_table('tag')
