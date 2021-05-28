import base64
import datetime
import json
import logging
from dataclasses import asdict, dataclass
from typing import AsyncIterator

from aiohttp import ClientResponseError
from neuro_auth_client import AuthClient, Permission
from yarl import URL

from platform_service_accounts_api.storage.base import (
    ServiceAccount as DBServiceAccount,
    ServiceAccountData,
    Storage,
)


logger = logging.getLogger()


@dataclass(frozen=True)
class AccountCreateData:
    name: str
    default_cluster: str
    role: str
    owner: str


@dataclass(frozen=True)
class ServiceAccount(DBServiceAccount):
    role_deleted: bool


@dataclass(frozen=True)
class ServiceAccountWithToken(ServiceAccount):
    token: str


class AccountsService:
    def __init__(
        self, storage: Storage, auth_client: AuthClient, api_base_url: URL
    ) -> None:
        self._storage = storage
        self._auth_client = auth_client
        self._api_base_url = api_base_url

    def _make_token_uri(self, account_id: str) -> str:
        return f"token://service_account/{account_id}"

    def _encode_token(self, auth_token: str, default_cluster: str) -> str:
        return base64.b64encode(
            json.dumps(
                {
                    "token": auth_token,
                    "cluster": default_cluster,
                    "url": str(self._api_base_url),
                }
            ).encode()
        ).decode()

    async def create(self, data: AccountCreateData) -> ServiceAccountWithToken:
        account = await self._storage.create(
            ServiceAccountData(
                **asdict(data),
                created_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        try:
            token_uri = self._make_token_uri(account.id)
            await self._auth_client.grant_user_permissions(
                data.role, [Permission(uri=token_uri, action="read")]
            )
            auth_token = await self._auth_client.get_user_token(
                data.role, new_token_uri=token_uri
            )
            token = self._encode_token(auth_token, account.default_cluster)
        except Exception:
            await self._storage.delete(account.id)
            raise
        return ServiceAccountWithToken(
            **asdict(account), role_deleted=False, token=token
        )

    async def _check_role_deleted(self, role: str) -> bool:
        try:
            await self._auth_client.get_user(role)
            return False
        except ClientResponseError:
            return True

    async def get(self, id: str) -> ServiceAccount:
        account = await self._storage.get(id)
        return ServiceAccount(
            **asdict(account), role_deleted=await self._check_role_deleted(account.role)
        )

    async def get_by_name(self, name: str, owner: str) -> ServiceAccount:
        account = await self._storage.get_by_name(name, owner)
        return ServiceAccount(
            **asdict(account), role_deleted=await self._check_role_deleted(account.role)
        )

    async def list(self, owner: str) -> AsyncIterator[ServiceAccount]:
        async for account in self._storage.list(owner):
            yield ServiceAccount(
                **asdict(account),
                role_deleted=await self._check_role_deleted(account.role),
            )

    async def delete(self, id: str) -> None:
        account = await self._storage.get(id)
        try:
            await self._auth_client.revoke_user_permissions(
                account.role, [self._make_token_uri(id)]
            )
        except ClientResponseError as e:
            if e.status == 400 and e.message == "Operation has no effect":
                # Token permission was already revoked
                pass
            raise
        await self._storage.delete(id)
