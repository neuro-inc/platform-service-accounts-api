import pytest
from asyncpg import Pool

from platform_service_accounts_api.storage.base import Storage
from platform_service_accounts_api.storage.postgres import PostgresStorage

from tests.unit.test_in_memory_storage import TestStorage as _TestAStorage


@pytest.fixture
def postgres_storage(postgres_pool: Pool) -> PostgresStorage:
    return PostgresStorage(postgres_pool)


class TestPostgresStorage(_TestAStorage):
    @pytest.fixture
    def storage(  # type: ignore
        self,
        postgres_storage: PostgresStorage,
    ) -> Storage:
        return postgres_storage
