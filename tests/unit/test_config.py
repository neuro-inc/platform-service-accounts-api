from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest
from yarl import URL

from platform_service_accounts_api.config import (
    Config,
    CORSConfig,
    PlatformAuthConfig,
    PostgresConfig,
    SentryConfig,
    ServerConfig,
    ZipkinConfig,
)
from platform_service_accounts_api.config_factory import EnvironConfigFactory

CA_DATA_PEM = "this-is-certificate-authority-public-key"
TOKEN = "this-is-token"


@pytest.fixture
def cert_authority_path(tmp_path: Path) -> str:
    ca_path = tmp_path / "ca.crt"
    ca_path.write_text(CA_DATA_PEM)
    return str(ca_path)


@pytest.fixture
def token_path(tmp_path: Path) -> str:
    token_path = tmp_path / "token"
    token_path.write_text(TOKEN)
    return str(token_path)


def test_create(cert_authority_path: str, token_path: str) -> None:
    environ: dict[str, Any] = {
        "NP_SERVICE_ACCOUNTS_API_HOST": "0.0.0.0",
        "NP_SERVICE_ACCOUNTS_API_PORT": 8080,
        "NP_SERVICE_ACCOUNTS_API_PLATFORM_AUTH_URL": "http://platformauthapi/api/v1",
        "NP_SERVICE_ACCOUNTS_API_PLATFORM_AUTH_TOKEN": "platform-auth-token",
        "NP_CORS_ORIGINS": "https://domain1.com,http://do.main",
        "NP_SERVICE_ACCOUNTS_API_ENABLE_DOCS": "true",
        "NP_ZIPKIN_URL": "http://zipkin:9411",
        "NP_SENTRY_DSN": "https://test.com",
        "NP_SENTRY_CLUSTER_NAME": "test",
        "NP_DB_POSTGRES_DSN": "postgresql://postgres@localhost:5432/postgres",
        "NP_DB_POSTGRES_POOL_MIN": "50",
        "NP_DB_POSTGRES_POOL_MAX": "500",
        "NP_SERVICE_ACCOUNTS_API_BASE_URL": "https://dev.neu.ro/api/v1",
    }
    config = EnvironConfigFactory(environ).create()
    assert config == Config(
        server=ServerConfig(host="0.0.0.0", port=8080),
        platform_auth=PlatformAuthConfig(
            url=URL("http://platformauthapi/api/v1"), token="platform-auth-token"
        ),
        postgres=PostgresConfig(
            postgres_dsn="postgresql://postgres@localhost:5432/postgres",
            pool_min_size=50,
            pool_max_size=500,
            alembic=ANY,
        ),
        cors=CORSConfig(["https://domain1.com", "http://do.main"]),
        zipkin=ZipkinConfig(url=URL("http://zipkin:9411")),
        sentry=SentryConfig(dsn=URL("https://test.com"), cluster_name="test"),
        enable_docs=True,
        api_base_url=URL("https://dev.neu.ro/api/v1"),
    )
