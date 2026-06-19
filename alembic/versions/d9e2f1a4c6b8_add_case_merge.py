"""add case merge lineage

Revision ID: d9e2f1a4c6b8
Revises: c7f3a9b2e4d1
Create Date: 2026-06-15 09:00:00.000000

Self-M2M case-merge lineage (source_case_id -> target_case_id). Single source of
truth for merge lineage; Case.duplicate_of_case_id is left for the separate manual
mark-as-duplicate feature. See docs/case-merge-design.md.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd9e2f1a4c6b8'
down_revision: Union[str, Sequence[str], None] = 'c7f3a9b2e4d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'case_merge',
        sa.Column('source_case_id', sa.Integer(), nullable=False),
        sa.Column('target_case_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['source_case_id'], ['case_.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_case_id'], ['case_.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('source_case_id', 'target_case_id'),
    )
    op.create_index(
        op.f('ix_case_merge_target_case_id'),
        'case_merge',
        ['target_case_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_case_merge_target_case_id'), table_name='case_merge')
    op.drop_table('case_merge')
