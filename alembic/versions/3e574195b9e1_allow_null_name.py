"""allow null name

Revision ID: 3e574195b9e1
Revises: 318f4b078ac9
Create Date: 2021-05-31 18:25:11.141195

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "3e574195b9e1"
down_revision = "318f4b078ac9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("service_accounts") as batch_op:
        batch_op.alter_column(column_name="name", nullable=True)


def downgrade():
    op.execute("UPDATE service_accounts SET name = random()::text WHERE name IS NULL")
    with op.batch_alter_table("service_accounts") as batch_op:
        batch_op.alter_column(
            column_name="name",
            nullable=False,
        )
