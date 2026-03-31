"""add model_version_id to jobs

Revision ID: 4cb2011651fa
Revises: 9f02320c4da1
Create Date: 2026-03-31 11:24:09.313881

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4cb2011651fa'
down_revision: Union[str, Sequence[str], None] = '9f02320c4da1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('model_version_id', sa.Integer(), nullable=True), schema='job')
    op.add_column('jobs', sa.Column('dataset_version_id', sa.Integer(), nullable=True), schema='job')


def downgrade() -> None:
    op.drop_column('jobs', 'dataset_version_id', schema='job')
    op.drop_column('jobs', 'model_version_id', schema='job')
