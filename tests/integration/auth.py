import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterator, Optional

import aiohttp
import pytest
from aiohttp.hdrs import AUTHORIZATION
from async_timeout import timeout
from docker import DockerClient
from docker.errors import NotFound as ContainerNotFound
from docker.models.containers import Container
from jose import jwt
from neuro_auth_client import AuthClient, Cluster, User
from neuro_auth_client.security import JWT_IDENTITY_CLAIM_OPTIONS
from yarl import URL

from platform_service_accounts_api.config import PlatformAuthConfig
from tests.integration.conftest import random_name


logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def auth_image() -> str:
    with open("AUTH_SERVER_IMAGE_NAME") as f:
        return f.read().strip()


@pytest.fixture(scope="session")
def auth_name() -> str:
    return "platform-service-accounts-api"


@pytest.fixture(scope="session")
def auth_jwt_secret() -> str:
    return os.environ.get("NP_JWT_SECRET", "secret")


def _create_url(container: Container, in_docker: bool) -> URL:
    exposed_port = 8080
    if in_docker:
        host, port = container.attrs["NetworkSettings"]["IPAddress"], exposed_port
    else:
        host, port = "0.0.0.0", container.ports[f"{exposed_port}/tcp"][0]["HostPort"]
    return URL(f"http://{host}:{port}")


@pytest.fixture(scope="session")
def _auth_url() -> URL:
    return URL(os.environ.get("AUTH_URL", ""))


@pytest.fixture(scope="session")
def _auth_server(
    docker_client: DockerClient,
    in_docker: bool,
    reuse_docker: bool,
    auth_image: str,
    auth_name: str,
    auth_jwt_secret: str,
    _auth_url: URL,
) -> Iterator[URL]:

    if _auth_url:
        yield _auth_url
        return

    try:
        container = docker_client.containers.get(auth_name)
        if reuse_docker:
            yield _create_url(container, in_docker)
            return
        else:
            container.remove(force=True)
    except ContainerNotFound:
        pass

    # `run` performs implicit `pull`
    container = docker_client.containers.run(
        image=auth_image,
        name=auth_name,
        publish_all_ports=True,
        stdout=False,
        stderr=False,
        detach=True,
        environment={"NP_JWT_SECRET": auth_jwt_secret},
    )
    container.reload()

    yield _create_url(container, in_docker)

    if not reuse_docker:
        container.remove(force=True)


async def wait_for_auth_server(
    url: URL, timeout_s: float = 300, interval_s: float = 1
) -> None:
    last_exc = None
    try:
        async with timeout(timeout_s):
            while True:
                try:
                    async with AuthClient(url=url, token="") as auth_client:
                        await auth_client.ping()
                        break
                except (AssertionError, OSError, aiohttp.ClientError) as exc:
                    last_exc = exc
                logger.debug(f"waiting for {url}: {last_exc}")
                await asyncio.sleep(interval_s)
    except asyncio.TimeoutError:
        pytest.fail(f"failed to connect to {url}: {last_exc}")


@pytest.fixture
async def auth_server(_auth_server: URL) -> AsyncIterator[URL]:
    await wait_for_auth_server(_auth_server)
    yield _auth_server


@pytest.fixture
def token_factory(auth_jwt_secret: str) -> Callable[[str], str]:
    def _factory(identity: str) -> str:
        payload = {claim: identity for claim in JWT_IDENTITY_CLAIM_OPTIONS}
        return jwt.encode(payload, auth_jwt_secret, algorithm="HS256")

    return _factory


@pytest.fixture
def admin_token(token_factory: Callable[[str], str]) -> str:
    return token_factory("admin")


@pytest.fixture
def cluster_token(token_factory: Callable[[str], str]) -> str:
    return token_factory("cluster")


@pytest.fixture
def no_claim_token(auth_jwt_secret: str) -> str:
    payload: Dict[str, Any] = {}
    return jwt.encode(payload, auth_jwt_secret, algorithm="HS256")


@pytest.fixture
async def auth_client(auth_server: URL, admin_token: str) -> AsyncIterator[AuthClient]:
    async with AuthClient(url=auth_server, token=admin_token) as client:
        yield client


@pytest.fixture
def auth_config(auth_server: URL, admin_token: str) -> PlatformAuthConfig:
    return PlatformAuthConfig(url=auth_server, token=admin_token)


@dataclass(frozen=True)
class _User(User):
    token: str = ""

    @property
    def headers(self) -> Dict[str, str]:
        return {AUTHORIZATION: f"Bearer {self.token}"}


@pytest.fixture
async def regular_user_factory(
    auth_client: AuthClient,
    token_factory: Callable[[str], str],
    admin_token: str,
    cluster_name: str,
) -> AsyncIterator[Callable[[Optional[str]], Awaitable[_User]]]:
    async def _factory(name: Optional[str] = None) -> _User:
        if not name:
            name = f"user-{random_name()}"
        user = User(name=name, clusters=[Cluster(name=cluster_name)])
        await auth_client.add_user(user, token=admin_token)
        return _User(name=user.name, token=token_factory(user.name))  # type: ignore

    yield _factory


@pytest.fixture
async def regular_user(regular_user_factory: Callable[[], Awaitable[_User]]) -> _User:
    return await regular_user_factory()
