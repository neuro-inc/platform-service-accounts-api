from dataclasses import dataclass
from typing import AsyncIterator, Callable

import aiohttp
import pytest
from aiohttp.web import HTTPOk
from aiohttp.web_exceptions import HTTPForbidden, HTTPUnauthorized

from platform_service_accounts_api.api import create_app
from platform_service_accounts_api.config import Config

from .conftest import ApiAddress, create_local_app_server


pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class ServiceAccountsApiEndpoints:
    address: ApiAddress

    @property
    def api_v1_endpoint(self) -> str:
        return f"http://{self.address.host}:{self.address.port}/api/v1"

    @property
    def ping_url(self) -> str:
        return f"{self.api_v1_endpoint}/ping"

    @property
    def secured_ping_url(self) -> str:
        return f"{self.api_v1_endpoint}/secured-ping"


@pytest.fixture
async def service_accounts_api(
    config: Config,
) -> AsyncIterator[ServiceAccountsApiEndpoints]:
    app = await create_app(config)
    async with create_local_app_server(app, port=8080) as address:
        yield ServiceAccountsApiEndpoints(address=address)


class TestApi:
    async def test_ping(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.get(service_accounts_api.ping_url) as resp:
            assert resp.status == HTTPOk.status_code
            text = await resp.text()
            assert text == "Pong"

    async def test_secured_ping(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
        admin_token: str,
    ) -> None:
        headers = {"Authorization": f"Bearer {admin_token}"}
        async with client.get(
            service_accounts_api.secured_ping_url, headers=headers
        ) as resp:
            assert resp.status == HTTPOk.status_code
            text = await resp.text()
            assert text == "Secured Pong"

    async def test_secured_ping_no_token_provided_unauthorized(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        url = service_accounts_api.secured_ping_url
        async with client.get(url) as resp:
            assert resp.status == HTTPUnauthorized.status_code

    async def test_secured_ping_non_existing_token_unauthorized(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
        token_factory: Callable[[str], str],
    ) -> None:
        url = service_accounts_api.secured_ping_url
        token = token_factory("non-existing-user")
        headers = {"Authorization": f"Bearer {token}"}
        async with client.get(url, headers=headers) as resp:
            assert resp.status == HTTPUnauthorized.status_code

    async def test_ping_unknown_origin(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.get(
            service_accounts_api.ping_url, headers={"Origin": "http://unknown"}
        ) as response:
            assert response.status == HTTPOk.status_code, await response.text()
            assert "Access-Control-Allow-Origin" not in response.headers

    async def test_ping_allowed_origin(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.get(
            service_accounts_api.ping_url, headers={"Origin": "https://neu.ro"}
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            assert resp.headers["Access-Control-Allow-Origin"] == "https://neu.ro"
            assert resp.headers["Access-Control-Allow-Credentials"] == "true"
            assert resp.headers["Access-Control-Expose-Headers"] == ""

    async def test_ping_options_no_headers(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.options(service_accounts_api.ping_url) as resp:
            assert resp.status == HTTPForbidden.status_code, await resp.text()
            assert await resp.text() == (
                "CORS preflight request failed: "
                "origin header is not specified in the request"
            )

    async def test_ping_options_unknown_origin(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.options(
            service_accounts_api.ping_url,
            headers={
                "Origin": "http://unknown",
                "Access-Control-Request-Method": "GET",
            },
        ) as resp:
            assert resp.status == HTTPForbidden.status_code, await resp.text()
            assert await resp.text() == (
                "CORS preflight request failed: "
                "origin 'http://unknown' is not allowed"
            )

    async def test_ping_options(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.options(
            service_accounts_api.ping_url,
            headers={
                "Origin": "https://neu.ro",
                "Access-Control-Request-Method": "GET",
            },
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            assert resp.headers["Access-Control-Allow-Origin"] == "https://neu.ro"
            assert resp.headers["Access-Control-Allow-Credentials"] == "true"
            assert resp.headers["Access-Control-Allow-Methods"] == "GET"
