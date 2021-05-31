import base64
import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable

import aiohttp
import pytest
from aiohttp import ClientResponseError
from aiohttp.web import HTTPOk
from aiohttp.web_exceptions import (
    HTTPCreated,
    HTTPForbidden,
    HTTPNoContent,
    HTTPNotFound,
    HTTPUnauthorized,
)
from neuro_auth_client import AuthClient, Cluster, User

from platform_service_accounts_api.api import create_app
from platform_service_accounts_api.config import Config

from .auth import _User
from .conftest import ApiAddress, create_local_app_server


pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class ServiceAccountsApiEndpoints:
    address: ApiAddress

    @property
    def server_base_url(self) -> str:
        return f"http://{self.address.host}:{self.address.port}"

    @property
    def api_v1_endpoint(self) -> str:
        return f"{self.server_base_url}/api/v1"

    @property
    def ping_url(self) -> str:
        return f"{self.api_v1_endpoint}/ping"

    @property
    def secured_ping_url(self) -> str:
        return f"{self.api_v1_endpoint}/secured-ping"

    @property
    def openapi_json_url(self) -> str:
        return f"{self.server_base_url}/api/docs/v1/service_accounts/swagger.json"

    @property
    def accounts_url(self) -> str:
        return f"{self.server_base_url}/api/v1/service_accounts"

    def account_url(self, id: str) -> str:
        return f"{self.accounts_url}/{id}"


@pytest.fixture
async def service_accounts_api(
    config: Config,
) -> AsyncIterator[ServiceAccountsApiEndpoints]:
    app = await create_app(config)
    async with create_local_app_server(app, port=8080) as address:
        yield ServiceAccountsApiEndpoints(address=address)


class TestApi:
    async def test_doc_available_when_enabled(
        self, config: Config, client: aiohttp.ClientSession
    ) -> None:
        config = replace(config, enable_docs=True)
        app = await create_app(config)
        async with create_local_app_server(app, port=8080) as address:
            endpoints = ServiceAccountsApiEndpoints(address=address)
            async with client.get(endpoints.openapi_json_url) as resp:
                assert resp.status == HTTPOk.status_code
                assert await resp.json()

    async def test_no_docs_when_disabled(
        self, config: Config, client: aiohttp.ClientSession
    ) -> None:
        config = replace(config, enable_docs=False)
        app = await create_app(config)
        async with create_local_app_server(app, port=8080) as address:
            endpoints = ServiceAccountsApiEndpoints(address=address)
            async with client.get(endpoints.openapi_json_url) as resp:
                assert resp.status == HTTPNotFound.status_code

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

    async def make_subrole(self, user: _User, auth_client: AuthClient) -> str:
        role_name = f"{user.name}/roles/test"
        role = User(name=role_name, clusters=[Cluster(name="default")])
        await auth_client.add_user(role)
        return role_name

    async def test_account_create(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            assert payload["name"] == "test"
            assert payload["owner"] == regular_user.name
            assert payload["default_cluster"] == "default"
            assert payload["role"] == role_name
            assert datetime.fromisoformat(payload["created_at"])
            assert not payload["role_deleted"]
            assert "id" in payload
            token = payload["token"]

        token_data = json.loads(base64.b64decode(token.encode()).decode())
        assert token_data["cluster"] == "default"
        assert token_data["url"] == "https://dev.neu.ro/api/v1"

        auth_token = token_data["token"]

        fetched_role = await auth_client.get_user(role_name, token=auth_token)
        assert fetched_role.name == role_name

    async def test_account_create_no_name(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()

    async def test_account_create_no_access_to_role(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user_factory: Callable[[], Awaitable[_User]],
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        user1 = await regular_user_factory()
        user2 = await regular_user_factory()
        role_name = await self.make_subrole(user1, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=user2.headers,
        ) as resp:
            assert resp.status == HTTPForbidden.status_code, await resp.text()

    async def test_account_get(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id = payload["id"]

        async with client.get(
            url=service_accounts_api.account_url(account_id),
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            payload = await resp.json()
            assert "token" not in payload
            assert payload["id"] == account_id
            assert payload["name"] == "test"
            assert payload["owner"] == regular_user.name
            assert payload["default_cluster"] == "default"
            assert payload["role"] == role_name
            assert datetime.fromisoformat(payload["created_at"])
            assert not payload["role_deleted"]

    async def test_account_get_by_name(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id = payload["id"]

        async with client.get(
            url=service_accounts_api.account_url("test"),
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            payload = await resp.json()
            assert "token" not in payload
            assert payload["id"] == account_id
            assert payload["name"] == "test"
            assert payload["owner"] == regular_user.name
            assert payload["default_cluster"] == "default"
            assert payload["role"] == role_name
            assert datetime.fromisoformat(payload["created_at"])
            assert not payload["role_deleted"]

    async def test_accounts_list_none(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
    ) -> None:
        async with client.get(
            url=service_accounts_api.accounts_url,
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            payloads = await resp.json()
            assert len(payloads) == 0

    async def test_accounts_list_one(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id = payload["id"]

        async with client.get(
            url=service_accounts_api.accounts_url,
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            payloads = await resp.json()
            assert len(payloads) == 1
            payload = payloads[0]
            assert "token" not in payload
            assert payload["id"] == account_id
            assert payload["name"] == "test"
            assert payload["owner"] == regular_user.name
            assert payload["default_cluster"] == "default"
            assert payload["role"] == role_name
            assert datetime.fromisoformat(payload["created_at"])
            assert not payload["role_deleted"]

    async def test_accounts_list_many(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id1 = payload["id"]

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test2", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id2 = payload["id"]

        async with client.get(
            url=service_accounts_api.accounts_url,
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPOk.status_code, await resp.text()
            payload = await resp.json()
            assert {item["id"] for item in payload} == {account_id1, account_id2}

    async def test_account_delete(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id = payload["id"]
            token = payload["token"]

        async with client.delete(
            url=service_accounts_api.account_url(account_id),
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPNoContent.status_code, await resp.text()

        token_data = json.loads(base64.b64decode(token.encode()).decode())
        auth_token = token_data["token"]

        with pytest.raises(ClientResponseError):
            await auth_client.get_user(role_name, token=auth_token)

    async def test_account_delete_role_deleted(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id = payload["id"]

        # Drop role
        path = auth_client._get_user_path(role_name)
        async with auth_client._request(method="DELETE", path=path):
            pass

        async with client.delete(
            url=service_accounts_api.account_url(account_id),
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPNoContent.status_code, await resp.text()

    async def test_account_delete_role_recreated(
        self,
        service_accounts_api: ServiceAccountsApiEndpoints,
        regular_user: _User,
        client: aiohttp.ClientSession,
        auth_client: AuthClient,
    ) -> None:
        role_name = await self.make_subrole(regular_user, auth_client)

        async with client.post(
            url=service_accounts_api.accounts_url,
            json={"name": "test", "role": role_name, "default_cluster": "default"},
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPCreated.status_code, await resp.text()
            payload = await resp.json()
            account_id = payload["id"]

        # Drop role
        path = auth_client._get_user_path(role_name)
        async with auth_client._request(method="DELETE", path=path):
            pass

        await auth_client.add_user(User(role_name))

        async with client.delete(
            url=service_accounts_api.account_url(account_id),
            headers=regular_user.headers,
        ) as resp:
            assert resp.status == HTTPNoContent.status_code, await resp.text()
