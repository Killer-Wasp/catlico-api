"""add custom fields

Revision ID: c7f3a9b2e4d1
Revises: b1c2d3e4f5a6
Create Date: 2026-06-14 13:40:00.000000

Org-scoped custom field definitions plus typed, polymorphic per-entity values
(EAV) over case/alert. The definition name is unique per org among live rows
(partial index, deleted_at IS NULL) so a name can be reused after soft-delete.
Backfills the new read/write:custom_field permissions onto the builtin roles.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7f3a9b2e4d1'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'custom_field',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'field_type',
            sa.Enum('string', 'integer', 'float', 'boolean', 'date', name='customfieldtype'),
            nullable=False,
        ),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('mandatory', sa.Boolean(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_custom_field_name'), 'custom_field', ['name'], unique=False)
    op.create_index(
        op.f('ix_custom_field_organisation_id'),
        'custom_field',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_custom_field_deleted_at'), 'custom_field', ['deleted_at'], unique=False
    )
    # Name is unique per org among live (non-deleted) definitions only.
    op.create_index(
        'uq_custom_field_name',
        'custom_field',
        ['name', 'organisation_id'],
        unique=True,
        sqlite_where=sa.text('deleted_at IS NULL'),
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    op.create_table(
        'custom_field_value',
        sa.Column('field_id', sa.Integer(), nullable=False),
        sa.Column(
            'entity_type',
            sa.Enum('case', 'alert', name='customfieldentitytype'),
            nullable=False,
        ),
        sa.Column('entity_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('string_value', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('integer_value', sa.Integer(), nullable=True),
        sa.Column('float_value', sa.Float(), nullable=True),
        sa.Column('boolean_value', sa.Boolean(), nullable=True),
        sa.Column('date_value', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['field_id'], ['custom_field.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('field_id', 'entity_type', 'entity_id'),
    )
    op.create_index(
        op.f('ix_custom_field_value_entity_id'),
        'custom_field_value',
        ['entity_id'],
        unique=False,
    )

    # Backfill the new permissions onto existing builtin roles. No-op on a fresh DB
    # where roles are seeded after migration (BUILTIN_ROLES seeding adds them then).
    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'read:custom_field' FROM role "
        "WHERE name IN ('org-admin', 'analyst', 'read-only')"
    )
    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'write:custom_field' FROM role WHERE name = 'org-admin'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "DELETE FROM role_permission "
        "WHERE permission IN ('read:custom_field', 'write:custom_field')"
    )
    op.drop_index(op.f('ix_custom_field_value_entity_id'), table_name='custom_field_value')
    op.drop_table('custom_field_value')
    op.drop_index('uq_custom_field_name', table_name='custom_field')
    op.drop_index(op.f('ix_custom_field_deleted_at'), table_name='custom_field')
    op.drop_index(op.f('ix_custom_field_organisation_id'), table_name='custom_field')
    op.drop_index(op.f('ix_custom_field_name'), table_name='custom_field')
    op.drop_table('custom_field')
    # Drop the enum types explicitly (Postgres; harmless no-op elsewhere).
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        sa.Enum(name='customfieldentitytype').drop(bind, checkfirst=True)
        sa.Enum(name='customfieldtype').drop(bind, checkfirst=True)
