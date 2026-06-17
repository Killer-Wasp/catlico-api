"""tasks logs improvements: due_date, occurred_at, soft-delete, per-org flag

Revision ID: d2f4a1b7c8e9
Revises: c1eeb01936c1
Create Date: 2026-06-13 13:10:00.000000

Folds the Tasks/Logs refinement schema changes into one migration:
- task: + due_date, + deleted_at/deleted_by, - flag
- log:  + occurred_at, + deleted_at/deleted_by
- case_: - flag
- new `flag` table (per-org polymorphic salience marker)
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2f4a1b7c8e9'
down_revision: Union[str, Sequence[str], None] = 'c1eeb01936c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'flag',
        sa.Column('entity_type', sa.Enum('case', 'task', name='flagentitytype'), nullable=False),
        sa.Column('entity_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('entity_type', 'entity_id', 'organisation_id'),
    )

    with op.batch_alter_table('task', schema=None) as batch_op:
        batch_op.add_column(sa.Column('due_date', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index(batch_op.f('ix_task_deleted_at'), ['deleted_at'], unique=False)
        batch_op.drop_column('flag')

    with op.batch_alter_table('log', schema=None) as batch_op:
        batch_op.add_column(sa.Column('occurred_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index(batch_op.f('ix_log_deleted_at'), ['deleted_at'], unique=False)

    with op.batch_alter_table('case_', schema=None) as batch_op:
        batch_op.drop_column('flag')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('case_', schema=None) as batch_op:
        batch_op.add_column(sa.Column('flag', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_log_deleted_at'))
        batch_op.drop_column('deleted_by')
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('occurred_at')

    with op.batch_alter_table('task', schema=None) as batch_op:
        batch_op.add_column(sa.Column('flag', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_index(batch_op.f('ix_task_deleted_at'))
        batch_op.drop_column('deleted_by')
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('due_date')

    op.drop_table('flag')
