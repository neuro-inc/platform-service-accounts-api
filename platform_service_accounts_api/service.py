import base64
import datetime
import json
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Optional

from aiohttp import ClientResponseError
from neuro_auth_client import AuthClient, User
from yarl import URL

from platform_service_accounts_api.storage.base import (
    ServiceAccount,
    ServiceAccountData,
    Storage,
)

logger = logging.getLogger()


class NoAccessToRoleError(Exception):
    pass


@dataclass(frozen=True)
class AccountCreateData:
    name: Optional[str]
    default_cluster: str
    owner: str
    default_project: str


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

    def _encode_token(self, auth_token: str, account: ServiceAccount) -> str:
        return base64.b64encode(
            json.dumps(
                {
                    "token": auth_token,
                    "cluster": account.default_cluster,
                    "url": str(self._api_base_url),
                    "project_name": account.default_project,
                }
            ).encode()
        ).decode()

    async def create(self, data: AccountCreateData) -> ServiceAccountWithToken:
        if data.name:
            role = f"{data.owner}/service-accounts/{data.name}"
        else:
            role = f"{data.owner}/service-accounts/{secrets.token_hex(8)}"

        account = await self._storage.create(
            ServiceAccountData(
                **asdict(data),
                role=role,
                created_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        try:
            await self._auth_client.add_user(User(name=role))
            auth_token = await self._auth_client.get_user_token(role)
            token = self._encode_token(auth_token, account)
        except Exception:
            await self._storage.delete(account.id)
            raise
        return ServiceAccountWithToken(**asdict(account), token=token)

    async def _check_no_such_role(self, role: str) -> bool:
        try:
            await self._auth_client.get_user(role)
            return False
        except ClientResponseError:
            return True

    async def get(self, id: str) -> ServiceAccount:
        return await self._storage.get(id)

    async def get_by_name(self, name: str, owner: str) -> ServiceAccount:
        return await self._storage.get_by_name(name, owner)

    async def list(self, owner: str) -> AsyncIterator[ServiceAccount]:
        async for account in self._storage.list(owner):
            yield ServiceAccount(
                **asdict(account),
            )

    async def delete(self, id: str) -> None:
        account = await self._storage.get(id)
        try:
            await self._auth_client.delete_user(account.role)
        except ClientResponseError as e:
            if e.status == 404:
                pass  # Role was deleted
            else:
                raise
        await self._storage.delete(id)
