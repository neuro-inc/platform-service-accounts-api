from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as sapg
import sqlalchemy.sql as sasql
from asyncpg import Connection, UniqueViolationError
from asyncpg.cursor import CursorFactory
from asyncpg.pool import Pool
from asyncpg.protocol.protocol import Record
from neuro_logging import trace
from sqlalchemy.dialects.postgresql import dialect

from .base import (
    ExistsError,
    NotExistsError,
    ServiceAccount,
    ServiceAccountData,
    Storage,
)

_postgresql_dialect = dialect(paramstyle="pyformat")


def _compile_query(query: sasql.ClauseElement) -> tuple[str, list[Any]]:
    compiled = query.compile(dialect=_postgresql_dialect)
    params = sorted(compiled.params.items())
    placeholders = {key: f"${index}" for index, (key, _) in enumerate(params, 1)}
    query_string = compiled.string % placeholders
    processors = compiled._bind_processors
    values = [
        processors[key](value) if key in processors else value for key, value in params
    ]
    return query_string, values


@dataclass(frozen=True)
class ServiceAccountTables:
    service_accounts: sa.Table
    metadata: sa.MetaData

    @classmethod
    def create(cls) -> ServiceAccountTables:
        metadata = sa.MetaData()
        service_accounts = sa.Table(
            "service_accounts",
            metadata,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("owner", sa.String(), nullable=False),
            sa.Column(
                "created_at", sapg.TIMESTAMP(timezone=True, precision=6), nullable=False
            ),
            sa.Column("payload", sapg.JSONB(), nullable=False),
            sa.Index("account_name_owner_uq", "name", "owner", unique=True),
        )
        return cls(
            service_accounts=service_accounts,
            metadata=metadata,
        )


class PostgresStorage(Storage):
    ID_PREFIX = "service-account"

    def __init__(
        self,
        pool: Pool,
    ):
        self._table = ServiceAccountTables.create().service_accounts
        self._pool = pool

    async def _execute(
        self, query: sasql.ClauseElement, conn: Connection | None = None
    ) -> str:
        query_string, params = _compile_query(query)
        conn = conn or self._pool
        return await conn.execute(query_string, *params)

    async def _fetchrow(
        self, query: sasql.ClauseElement, conn: Connection | None = None
    ) -> Record | None:
        query_string, params = _compile_query(query)
        conn = conn or self._pool
        return await conn.fetchrow(query_string, *params)

    def _cursor(self, query: sasql.ClauseElement, conn: Connection) -> CursorFactory:
        query_string, params = _compile_query(query)
        return conn.cursor(query_string, *params)

    def _gen_id(self) -> str:
        return f"{self.ID_PREFIX}-{uuid.uuid4()}"

    def _to_values(self, item: ServiceAccount) -> dict[str, Any]:
        payload = asdict(item)
        return {
            "id": payload.pop("id"),
            "name": payload.pop("name"),
            "owner": payload.pop("owner"),
            "created_at": payload.pop("created_at"),
            "payload": payload,
        }

    def _from_record(self, record: Record) -> ServiceAccount:
        payload = json.loads(record["payload"])
        payload["id"] = record["id"]
        payload["name"] = record["name"]
        payload["owner"] = record["owner"]
        payload["created_at"] = record["created_at"]
        return ServiceAccount(**payload)

    @trace
    async def create(self, data: ServiceAccountData) -> ServiceAccount:
        entry = ServiceAccount.from_data_obj(self._gen_id(), data)
        values = self._to_values(entry)
        query = self._table.insert().values(values)
        try:
            await self._execute(query)
        except UniqueViolationError:
            raise ExistsError
        return entry

    @trace
    async def get(self, id: str) -> ServiceAccount:
        query = self._table.select(self._table.c.id == id)
        record = await self._fetchrow(query)
        if not record:
            raise NotExistsError
        return self._from_record(record)

    @trace
    async def get_by_name(
        self,
        name: str,
        owner: str,
    ) -> ServiceAccount:
        query = (
            self._table.select()
            .where(self._table.c.owner == owner)
            .where(self._table.c.name == name)
        )
        record = await self._fetchrow(query)
        if not record:
            raise NotExistsError
        return self._from_record(record)

    async def delete(self, id: str) -> None:
        query = self._table.delete().where(self._table.c.id == id)
        await self._execute(query)

    async def list(
        self,
        owner: str | None = None,
    ) -> AsyncIterator[ServiceAccount]:
        query = self._table.select()
        if owner is not None:
            query = query.where(self._table.c.owner == owner)
        async with self._pool.acquire() as conn, conn.transaction():
            async for record in self._cursor(query, conn=conn):
                yield self._from_record(record)
