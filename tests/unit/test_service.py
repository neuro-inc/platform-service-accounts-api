import base64
import json
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

import pytest
from aiohttp import ClientResponseError
from neuro_auth_client import AuthClient, Cluster, Permission, User
from yarl import URL

from platform_service_accounts_api.service import (
    AccountCreateData,
    AccountsService,
    NoAccessToRoleError,
    ServiceAccount,
)
from platform_service_accounts_api.storage.base import NotExistsError
from platform_service_accounts_api.storage.in_memory import InMemoryStorage


pytestmark = pytest.mark.asyncio


class MockAuthClient(AuthClient):
    def __init__(self) -> None:
        self.user_to_return: Optional[User] = User(
            name="testuser",
            clusters=[
                Cluster("default"),
            ],
        )
        self.check_perm_return = True
        self._grants: List[Tuple[str, Sequence[Permission]]] = []
        self._revokes: List[Tuple[str, Sequence[str]]] = []

    async def get_user(self, name: str, token: Optional[str] = None) -> User:
        if self.user_to_return:
            return self.user_to_return
        else:
            raise ClientResponseError(
                request_info=None,  # type: ignore
                history=(),
                status=404,
            )

    @property
    def grants(self) -> List[Tuple[str, Sequence[Permission]]]:
        return self._grants

    @property
    def revokes(self) -> List[Tuple[str, Sequence[str]]]:
        return self._revokes

    async def grant_user_permissions(
        self, name: str, permissions: Sequence[Permission], token: Optional[str] = None
    ) -> None:
        self._grants.append((name, permissions))

    async def revoke_user_permissions(
        self, name: str, resources_uris: Sequence[str], token: Optional[str] = None
    ) -> None:
        self._revokes.append((name, resources_uris))

    async def get_user_token(
        self,
        name: str,
        new_token_uri: Optional[str] = None,
        token: Optional[str] = None,
    ) -> str:
        return f"token-{name}"

    async def check_user_permissions(
        self, name: str, permissions: Sequence[Permission], token: Optional[str] = None
    ) -> bool:
        return self.check_perm_return


class TestService:
    CREATE_DATA = AccountCreateData(
        name="test",
        role="testrole",
        owner="testowner",
        default_cluster="default",
    )

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
        before_create = datetime.now(timezone.utc)
        account = await service.create(self.CREATE_DATA)
        after_create = datetime.now(timezone.utc)
        assert account.id
        assert account.name == self.CREATE_DATA.name
        assert account.role == self.CREATE_DATA.role
        assert account.owner == self.CREATE_DATA.owner
        assert account.default_cluster == self.CREATE_DATA.default_cluster
        assert account.created_at >= before_create
        assert account.created_at <= after_create
        assert not account.role_deleted
        token = account.token
        token_data = json.loads(base64.b64decode(token.encode()).decode())
        assert token_data["token"] == f"token-{self.CREATE_DATA.role}"
        assert token_data["cluster"] == self.CREATE_DATA.default_cluster
        assert token_data["url"] == "https://dev.neu.ro/api/v1"

        username, perms = mock_auth_client.grants[0]
        assert username == self.CREATE_DATA.role
        assert perms[0].uri == f"token://service_account/{account.id}"

    async def test_create_no_perm(
        self, service: AccountsService, mock_auth_client: MockAuthClient
    ) -> None:
        mock_auth_client.check_perm_return = False
        with pytest.raises(NoAccessToRoleError):
            await service.create(self.CREATE_DATA)

    async def test_get(self, service: AccountsService) -> None:
        account = await service.create(self.CREATE_DATA)
        get_res = await service.get(account.id)
        assert ServiceAccount.__eq__(account, get_res)

    async def test_get_role_removed(
        self, service: AccountsService, mock_auth_client: MockAuthClient
    ) -> None:
        account = await service.create(self.CREATE_DATA)
        mock_auth_client.user_to_return = None
        get_res = await service.get(account.id)
        assert get_res.role_deleted

    async def test_list(self, service: AccountsService) -> None:
        account = await service.create(self.CREATE_DATA)
        async for list_res in service.list(owner=account.owner):
            assert ServiceAccount.__eq__(account, list_res)

    async def test_list_empty(self, service: AccountsService) -> None:
        async for _ in service.list(owner="test"):
            assert False

    async def test_delete(
        self, service: AccountsService, mock_auth_client: MockAuthClient
    ) -> None:
        account = await service.create(self.CREATE_DATA)
        await service.delete(account.id)
        with pytest.raises(NotExistsError):
            await service.get(account.id)

        username, uris = mock_auth_client.revokes[0]
        assert username == account.role
        assert uris[0] == f"token://service_account/{account.id}"
