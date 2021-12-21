import secrets
from collections.abc import AsyncIterator
from typing import Optional

from .base import (
    ExistsError,
    NotExistsError,
    ServiceAccount,
    ServiceAccountData,
    Storage,
)


class InMemoryStorage(Storage):
    def __init__(self) -> None:
        self._items: dict[str, ServiceAccount] = {}

    def _gen_id(self) -> str:
        return secrets.token_hex(8)

    async def create(self, data: ServiceAccountData) -> ServiceAccount:
        await self.check_exists(data)
        new_id = self._gen_id()
        account = ServiceAccount.from_data_obj(new_id, data)
        self._items[new_id] = account
        return account

    async def get(self, id: str) -> ServiceAccount:
        if id not in self._items:
            raise NotExistsError
        return self._items[id]

    async def get_by_name(self, name: str, owner: str) -> ServiceAccount:
        for item in self._items.values():
            if item.name == name and item.owner == owner:
                return item
        raise NotExistsError

    async def delete(self, id: str) -> None:
        self._items.pop(id)

    async def list(self, owner: Optional[str] = None) -> AsyncIterator[ServiceAccount]:
        for item in self._items.values():
            if owner is not None and item.owner != owner:
                continue
            yield item

    async def check_exists(self, data: ServiceAccountData) -> None:
        name = data.name
        if name is None:
            return
        try:
            await self.get_by_name(name, data.owner)
        except NotExistsError:
            return
        raise ExistsError
