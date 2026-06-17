"""add observables and comments

Revision ID: f4b6c3d0a2e1
Revises: e3a5b2c9d1f0
Create Date: 2026-06-13 14:05:00.000000

String observables (inline data, no blob storage yet), polymorphic over case/alert,
with task_share-style observable_share fan-out and a within-case partial-unique dedup.
Plus flat polymorphic case comments. Observable types live in a seeded registry.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4b6c3d0a2e1'
down_revision: Union[str, Sequence[str], None] = 'e3a5b2c9d1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'observable_type',
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('is_attachment', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )

    op.create_table(
        'observable',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=True),
        sa.Column('alert_id', sa.Integer(), nullable=True),
        sa.Column('observable_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('data', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('message', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('tlp', sa.Integer(), nullable=False),
        sa.Column('ioc', sa.Boolean(), nullable=False),
        sa.Column('sighted', sa.Boolean(), nullable=False),
        sa.Column('ignore_similarity', sa.Boolean(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.CheckConstraint(
            'case_id IS NOT NULL OR alert_id IS NOT NULL', name='ck_observable_parent'
        ),
        sa.ForeignKeyConstraint(['alert_id'], ['alert.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['case_id'], ['case_.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['observable_type'], ['observable_type.name']),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_observable_case_id'), 'observable', ['case_id'], unique=False)
    op.create_index(op.f('ix_observable_alert_id'), 'observable', ['alert_id'], unique=False)
    op.create_index(op.f('ix_observable_data'), 'observable', ['data'], unique=False)
    op.create_index(
        op.f('ix_observable_organisation_id'), 'observable', ['organisation_id'], unique=False
    )
    op.create_index(op.f('ix_observable_deleted_at'), 'observable', ['deleted_at'], unique=False)
    op.create_index(
        'uq_observable_case_dedup',
        'observable',
        ['case_id', 'observable_type', 'data'],
        unique=True,
        sqlite_where=sa.text('deleted_at IS NULL AND case_id IS NOT NULL'),
        postgresql_where=sa.text('deleted_at IS NULL AND case_id IS NOT NULL'),
    )

    op.create_table(
        'observable_share',
        sa.Column('observable_id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['observable_id'], ['observable.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('observable_id', 'organisation_id'),
    )

    op.create_table(
        'comment',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'entity_type', sa.Enum('case', name='commententitytype'), nullable=False
        ),
        sa.Column('entity_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('message', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_comment_entity_type'), 'comment', ['entity_type'], unique=False)
    op.create_index(op.f('ix_comment_entity_id'), 'comment', ['entity_id'], unique=False)
    op.create_index(
        op.f('ix_comment_organisation_id'), 'comment', ['organisation_id'], unique=False
    )
    op.create_index(op.f('ix_comment_deleted_at'), 'comment', ['deleted_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('comment')
    op.drop_table('observable_share')
    op.drop_index('uq_observable_case_dedup', table_name='observable')
    op.drop_table('observable')
    op.drop_table('observable_type')
