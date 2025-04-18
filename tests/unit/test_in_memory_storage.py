import secrets
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

import pytest

from platform_service_accounts_api.storage.base import (
    ExistsError,
    NotExistsError,
    ServiceAccountData,
    Storage,
)
from platform_service_accounts_api.storage.in_memory import InMemoryStorage


@pytest.fixture
def in_memory_storage() -> InMemoryStorage:
    return InMemoryStorage()


class TestStorage:
    @pytest.fixture
    def storage(self, in_memory_storage: InMemoryStorage) -> Storage:
        return in_memory_storage

    def compare_data(
        self, data1: ServiceAccountData, data2: ServiceAccountData
    ) -> None:
        d1 = asdict(data1)
        d1.pop("id", None)
        d2 = asdict(data2)
        d2.pop("id", None)
        assert d1 == d2

    async def gen_data(self, **kwargs: Any) -> ServiceAccountData:
        data = ServiceAccountData(
            name=secrets.token_hex(8),
            owner=secrets.token_hex(8),
            role=secrets.token_hex(8),
            default_cluster=secrets.token_hex(8),
            default_project=secrets.token_hex(8),
            default_org=None,
            created_at=datetime.now(UTC),
        )
        # Updating this way so constructor call is typechecked properly
        for key, value in kwargs.items():
            data = replace(data, **{key: value})
        return data

    async def test_create_get(self, storage: Storage) -> None:
        data = await self.gen_data()
        created = await storage.create(data)
        self.compare_data(data, created)
        res = await storage.get(created.id)
        self.compare_data(res, created)
        assert res.id == created.id

    async def test_create_get_no_name(self, storage: Storage) -> None:
        data = await self.gen_data(name=None)
        created = await storage.create(data)
        self.compare_data(data, created)
        res = await storage.get(created.id)
        self.compare_data(res, created)
        assert res.id == created.id

    async def test_get_not_exists(self, storage: Storage) -> None:
        with pytest.raises(NotExistsError):
            await storage.get("wrong-id")

    async def test_delete(self, storage: Storage) -> None:
        data = await self.gen_data()
        created = await storage.create(data)
        await storage.delete(created.id)
        with pytest.raises(NotExistsError):
            await storage.get(created.id)

    async def test_get_by_name(self, storage: Storage) -> None:
        data = await self.gen_data()
        res = await storage.create(data)
        assert data.name is not None
        account = await storage.get_by_name(name=data.name, owner=data.owner)
        assert account.id == res.id

    async def test_get_by_name_wrong_owner(self, storage: Storage) -> None:
        data = await self.gen_data()
        await storage.create(data)
        assert data.name is not None
        with pytest.raises(NotExistsError):
            await storage.get_by_name(
                name=data.name,
                owner="wrong_owner",
            )

    async def test_cannot_create_duplicate(self, storage: Storage) -> None:
        data = await self.gen_data()
        await storage.create(data)
        with pytest.raises(ExistsError):
            await storage.create(data)

    async def test_list(self, storage: Storage) -> None:
        for name_id in range(5):
            for owner_id in range(5):
                data = await self.gen_data(
                    name=f"name-{name_id}",
                    owner=f"owner-{owner_id}",
                )
                await storage.create(data)
        found = []
        async for item in storage.list(owner="owner-2"):
            found.append(item.name)
        assert len(found) == 5
        assert set(found) == {f"name-{index}" for index in range(5)}
