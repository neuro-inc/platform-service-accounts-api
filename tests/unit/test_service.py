import base64
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from aiohttp import ClientResponseError
from neuro_auth_client import AuthClient, User
from yarl import URL

from platform_service_accounts_api.service import AccountCreateData, AccountsService
from platform_service_accounts_api.storage.base import (
    NotExistsError,
    ServiceAccountData,
)
from platform_service_accounts_api.storage.in_memory import InMemoryStorage


class MockAuthClient(AuthClient):
    def __init__(self) -> None:
        self.user_to_return: User | None = User(name="testuser")
        self.created_users: list[User] = []
        self.deleted_users: list[str] = []

    async def add_user(self, user: User, token: str | None = None) -> None:
        self.created_users.append(user)

    async def delete_user(self, name: str, token: str | None = None) -> None:
        self.deleted_users.append(name)

    async def get_user(self, name: str, token: str | None = None) -> User:
        if self.user_to_return:
            return self.user_to_return
        raise ClientResponseError(
            request_info=None,  # type: ignore
            history=(),
            status=404,
        )

    async def get_user_token(
        self,
        name: str,
        new_token_uri: str | None = None,
        token: str | None = None,
    ) -> str:
        return f"token-{name}"


class TestService:
    CREATE_DATA = AccountCreateData(
        name="test",
        owner="testowner",
        default_cluster="default",
        default_project="default-project",
        default_org=None,
    )

    def compare_data(
        self, data1: ServiceAccountData, data2: ServiceAccountData
    ) -> None:
        d1 = asdict(data1)
        d1.pop("id", None)
        d1.pop("token", None)
        d2 = asdict(data2)
        d2.pop("id", None)
        d2.pop("token", None)
        assert d1 == d2

    @pytest.fixture
    def mock_auth_client(self) -> MockAuthClient:
        return MockAuthClient()

    @pytest.fixture
    def service(self, mock_auth_client: MockAuthClient) -> AccountsService:
        return AccountsService(
            storage=InMemoryStorage(),
            auth_client=mock_auth_client,
            api_base_url=URL("https://dev.neu.ro/api/v1"),
        )

    async def test_create(
        self, service: AccountsService, mock_auth_client: MockAuthClient
    ) -> None:
        before_create = datetime.now(UTC)
        account = await service.create(self.CREATE_DATA)
        after_create = datetime.now(UTC)
        expected_role = (
            f"{self.CREATE_DATA.owner}/service-accounts/{self.CREATE_DATA.name}"
        )
        assert account.id
        assert account.name == self.CREATE_DATA.name
        assert account.role == expected_role
        assert account.owner == self.CREATE_DATA.owner
        assert account.default_cluster == self.CREATE_DATA.default_cluster
        assert account.created_at >= before_create
        assert account.created_at <= after_create
        token = account.token
        token_data = json.loads(base64.b64decode(token.encode()).decode())
        assert token_data["token"] == f"token-{expected_role}"
        assert token_data["cluster"] == self.CREATE_DATA.default_cluster
        assert token_data["url"] == "https://dev.neu.ro/api/v1"
        assert token_data["project_name"] == self.CREATE_DATA.default_project
        assert mock_auth_client.created_users[0].name == expected_role

    async def test_create_no_name(
        self, service: AccountsService, mock_auth_client: MockAuthClient
    ) -> None:
        data = replace(self.CREATE_DATA, name=None)
        account = await service.create(data)
        assert account.role.startswith(f"{self.CREATE_DATA.owner}/service-accounts")

    async def test_get(self, service: AccountsService) -> None:
        account = await service.create(self.CREATE_DATA)
        get_res = await service.get(account.id)
        self.compare_data(account, get_res)

    async def test_list(self, service: AccountsService) -> None:
        account = await service.create(self.CREATE_DATA)
        async for list_res in service.list(owner=account.owner):
            self.compare_data(account, list_res)

    async def test_list_empty(self, service: AccountsService) -> None:
        async for _ in service.list(owner="test"):
            raise AssertionError

    async def test_delete(
        self, service: AccountsService, mock_auth_client: MockAuthClient
    ) -> None:
        account = await service.create(self.CREATE_DATA)
        await service.delete(account.id)
        with pytest.raises(NotExistsError):
            await service.get(account.id)

        assert mock_auth_client.deleted_users[0] == account.role
