"""add attachments

Revision ID: a7c9e2f5b3d4
Revises: f4b6c3d0a2e1
Create Date: 2026-06-13 14:30:00.000000

Content-addressed blob metadata (attachment, deduped by sha256) plus a polymorphic
attachment_link from owners (observable | log) to blobs. Bytes live in fsspec-backed
storage; only metadata is in the DB.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c9e2f5b3d4'
down_revision: Union[str, Sequence[str], None] = 'f4b6c3d0a2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'attachment',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_attachment_sha256'), 'attachment', ['sha256'], unique=True)

    op.create_table(
        'attachment_link',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('attachment_id', sa.Uuid(), nullable=False),
        sa.Column(
            'owner_type',
            sa.Enum('observable', 'log', name='attachmentownertype'),
            nullable=False,
        ),
        sa.Column('owner_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['attachment_id'], ['attachment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_attachment_link_owner_type'), 'attachment_link', ['owner_type'], unique=False
    )
    op.create_index(
        op.f('ix_attachment_link_owner_id'), 'attachment_link', ['owner_id'], unique=False
    )
    op.create_index(
        op.f('ix_attachment_link_organisation_id'),
        'attachment_link',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_attachment_link_deleted_at'), 'attachment_link', ['deleted_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('attachment_link')
    op.drop_index(op.f('ix_attachment_sha256'), table_name='attachment')
    op.drop_table('attachment')
