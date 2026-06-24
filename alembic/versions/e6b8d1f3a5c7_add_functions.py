"""add functions

Revision ID: e6b8d1f3a5c7
Revises: d5a7c9e2f4b6
Create Date: 2026-06-24 12:04:00.000000

Org-scoped automations (Function) plus immutable execution records (FunctionRun).
Backfills read/run/write:function permissions onto builtin roles.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'e6b8d1f3a5c7'
down_revision: Union[str, Sequence[str], None] = 'd5a7c9e2f4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'function',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column(
            'runtime',
            sa.Enum('javascript', 'python', name='functionruntime'),
            nullable=False,
        ),
        sa.Column(
            'trigger',
            sa.Enum('scheduled', 'event', 'manual', 'api', name='functiontrigger'),
            nullable=False,
        ),
        sa.Column('trigger_config', sa.JSON(), nullable=False),
        sa.Column('profile', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='analyst'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('timeout_ms', sa.Integer(), nullable=False, server_default='15000'),
        sa.Column('egress', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('approval', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('code', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('secrets', sa.JSON(), nullable=False),
        sa.Column('run_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_function_organisation_id'), 'function', ['organisation_id'], unique=False
    )
    op.create_index(op.f('ix_function_name'), 'function', ['name'], unique=False)
    op.create_index(op.f('ix_function_deleted_at'), 'function', ['deleted_at'], unique=False)

    op.create_table(
        'function_run',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('function_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('success', 'failure', name='functionrunstatus'),
            nullable=False,
        ),
        sa.Column('trigger', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['function_id'], ['function.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_function_run_function_id'),
        'function_run',
        ['function_id'],
        unique=False,
    )

    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'read:function' FROM role "
        "WHERE name IN ('org-admin', 'analyst', 'read-only')"
    )
    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'run:function' FROM role WHERE name IN ('org-admin', 'analyst')"
    )
    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'write:function' FROM role WHERE name = 'org-admin'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permission "
        "WHERE permission IN ('read:function', 'run:function', 'write:function')"
    )
    op.drop_index(op.f('ix_function_run_function_id'), table_name='function_run')
    op.drop_table('function_run')
    op.drop_index(op.f('ix_function_deleted_at'), table_name='function')
    op.drop_index(op.f('ix_function_name'), table_name='function')
    op.drop_index(op.f('ix_function_organisation_id'), table_name='function')
    op.drop_table('function')
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        sa.Enum(name='functionrunstatus').drop(bind, checkfirst=True)
        sa.Enum(name='functiontrigger').drop(bind, checkfirst=True)
        sa.Enum(name='functionruntime').drop(bind, checkfirst=True)
