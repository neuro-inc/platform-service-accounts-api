"""create service account table

Revision ID: 318f4b078ac9
Revises:
Create Date: 2021-05-28 17:14:42.458821

"""
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as sapg

from alembic import op

# revision identifiers, used by Alembic.
revision = "318f4b078ac9"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column(
            "created_at", sapg.TIMESTAMP(timezone=True, precision=6), nullable=False
        ),
        sa.Column("payload", sapg.JSONB(), nullable=False),
    )
    op.create_index(
        "account_name_owner_uq",
        "service_accounts",
        ["name", "owner"],
        unique=True,
    )


def downgrade():
    op.drop_table("service_accounts")
