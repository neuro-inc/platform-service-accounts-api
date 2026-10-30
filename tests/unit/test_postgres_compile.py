import sqlalchemy as sa

from platform_service_accounts_api.storage.postgres import _compile_query


def test_compile_query_uses_asyncpg_placeholders() -> None:
    table = sa.table(
        "accounts", sa.column("id", sa.String), sa.column("name", sa.String)
    )

    query, params = _compile_query(table.insert().values(id="account-1", name="user"))

    assert query == "INSERT INTO accounts (id, name) VALUES ($1, $2)"
    assert params == ["account-1", "user"]
