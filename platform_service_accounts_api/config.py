from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from yarl import URL

from alembic.config import Config as AlembicConfig


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass(frozen=True)
class PlatformAuthConfig:
    url: URL
    token: str = field(repr=False)


@dataclass(frozen=True)
class CORSConfig:
    allowed_origins: Sequence[str] = ()


@dataclass(frozen=True)
class PostgresConfig:
    postgres_dsn: str

    alembic: AlembicConfig

    # based on defaults
    # https://magicstack.github.io/asyncpg/current/api/index.html#asyncpg.connection.connect
    pool_min_size: int = 10
    pool_max_size: int = 10

    connect_timeout_s: float = 60.0
    command_timeout_s: float | None = 60.0


@dataclass(frozen=True)
class Config:
    api_base_url: URL
    server: ServerConfig
    platform_auth: PlatformAuthConfig
    cors: CORSConfig
    postgres: PostgresConfig
    enable_docs: bool = False
