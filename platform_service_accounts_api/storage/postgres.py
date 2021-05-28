import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Dict, Optional

import asyncpgsa
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as sapg
import sqlalchemy.sql as sasql
from asyncpg import Connection, UniqueViolationError
from asyncpg.cursor import CursorFactory
from asyncpg.pool import Pool
from asyncpg.protocol.protocol import Record
from platform_logging import trace

from .base import (
    ExistsError,
    NotExistsError,
    ServiceAccount,
    ServiceAccountData,
    Storage,
)


@dataclass(frozen=True)
class ServiceAccountTables:
    service_accounts: sa.Table

    @classmethod
    def create(cls) -> "ServiceAccountTables":
        metadata = sa.MetaData()
        service_accounts = sa.Table(
            "service_accounts",
            metadata,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("owner", sa.String(), nullable=False),
            sa.Column(
                "created_at", sapg.TIMESTAMP(timezone=True, precision=6), nullable=False
            ),
            sa.Column("payload", sapg.JSONB(), nullable=False),
        )
        return cls(
            service_accounts=service_accounts,
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
        self, query: sasql.ClauseElement, conn: Optional[Connection] = None
    ) -> str:
        query_string, params = asyncpgsa.compile_query(query)
        conn = conn or self._pool
        return await conn.execute(query_string, *params)

    async def _fetchrow(
        self, query: sasql.ClauseElement, conn: Optional[Connection] = None
    ) -> Optional[Record]:
        query_string, params = asyncpgsa.compile_query(query)
        conn = conn or self._pool
        return await conn.fetchrow(query_string, *params)

    def _cursor(self, query: sasql.ClauseElement, conn: Connection) -> CursorFactory:
        query_string, params = asyncpgsa.compile_query(query)
        return conn.cursor(query_string, *params)

    def _gen_id(self) -> str:
        return f"{self.ID_PREFIX}-{uuid.uuid4()}"

    def _to_values(self, item: ServiceAccount) -> Dict[str, Any]:
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

    async def list(
        self,
        owner: Optional[str] = None,
    ) -> AsyncIterator[ServiceAccount]:
        query = self._table.select()
        if owner is not None:
            query = query.where(self._table.c.owner == owner)
        async with self._pool.acquire() as conn, conn.transaction():
            async for record in self._cursor(query, conn=conn):
                yield self._from_record(record)
