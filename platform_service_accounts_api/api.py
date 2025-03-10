import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import version

import aiohttp
import aiohttp.web
import aiohttp_cors
from aiohttp.web import (
    HTTPBadRequest,
    HTTPInternalServerError,
    Request,
    Response,
    StreamResponse,
    json_response,
    middleware,
)
from aiohttp.web_exceptions import (
    HTTPConflict,
    HTTPCreated,
    HTTPForbidden,
    HTTPNoContent,
    HTTPNotFound,
    HTTPOk,
)
from aiohttp_apispec import docs, request_schema, response_schema, setup_aiohttp_apispec
from aiohttp_security import check_authorized
from marshmallow import ValidationError
from neuro_auth_client import AuthClient, User
from neuro_auth_client.security import AuthScheme, setup_security
from neuro_logging import init_logging, notrace, setup_sentry, setup_zipkin_tracer

from .config import Config, CORSConfig, PlatformAuthConfig
from .config_factory import EnvironConfigFactory
from .identity import untrusted_user
from .postgres import create_postgres_pool
from .schema import (
    ClientErrorSchema,
    ServiceAccountCreateSchema,
    ServiceAccountSchema,
    ServiceAccountWithTokenSchema,
)
from .service import (
    AccountCreateData,
    AccountsService,
    NoAccessToRoleError,
    ServiceAccount,
)
from .storage.base import ExistsError, NotExistsError, Storage
from .storage.postgres import PostgresStorage
from .utils import accepts_ndjson, auto_close, ndjson_error_handler

logger = logging.getLogger(__name__)


CONFIG: aiohttp.web.AppKey[Config] = aiohttp.web.AppKey("CONFIG", Config)
API_V1_APP: aiohttp.web.AppKey[aiohttp.web.Application] = aiohttp.web.AppKey("API_V1_APP", aiohttp.web.Application)


class ApiHandler:
    def register(self, app: aiohttp.web.Application) -> None:
        app.add_routes(
            [
                aiohttp.web.get("/ping", self.handle_ping),
                aiohttp.web.get("/secured-ping", self.handle_secured_ping),
            ]
        )

    @notrace
    async def handle_ping(self, request: Request) -> Response:
        return Response(text="Pong")

    @notrace
    async def handle_secured_ping(self, request: Request) -> Response:
        await check_authorized(request)
        return Response(text="Secured Pong")


class ServiceAccountsApiHandler:
    def __init__(self, app: aiohttp.web.Application, config: Config) -> None:
        self._app = app
        self._config = config

    def register(self, app: aiohttp.web.Application) -> None:
        app.add_routes(
            [
                aiohttp.web.get("", self.list),
                aiohttp.web.post("", self.create),
                aiohttp.web.get("/{id_or_name}", self.get),
                aiohttp.web.delete("/{id_or_name}", self.delete),
            ]
        )

    @property
    def service(self) -> AccountsService:
        return self._app["service"]

    async def _get_untrusted_user(self, request: Request) -> User:
        identity = await untrusted_user(request)
        return User(name=identity.name)

    async def _resolve_service_account(self, request: Request) -> ServiceAccount:
        id_or_name = request.match_info["id_or_name"]
        try:
            account = await self.service.get(id_or_name)
        except NotExistsError:
            user = await self._get_untrusted_user(request)
            try:
                account = await self.service.get_by_name(id_or_name, user.name)
            except NotExistsError:
                raise HTTPNotFound(text=f"Service account {id_or_name} not found")
        return account

    @docs(
        tags=["service_accounts"],
        summary="List all Service Accounts current user has access",
    )
    @response_schema(ServiceAccountSchema(many=True), HTTPOk.status_code)
    async def list(
        self,
        request: aiohttp.web.Request,
    ) -> aiohttp.web.StreamResponse:
        username = await check_authorized(request)
        bake_images = self.service.list(owner=username)
        async with auto_close(bake_images):  # type: ignore[arg-type]
            if accepts_ndjson(request):
                response = aiohttp.web.StreamResponse()
                response.headers["Content-Type"] = "application/x-ndjson"
                await response.prepare(request)
                async with ndjson_error_handler(request, response):
                    async for image in bake_images:
                        payload_line = ServiceAccountSchema().dumps(image)
                        await response.write(payload_line.encode() + b"\n")
                return response
            else:
                response_payload = [
                    ServiceAccountSchema().dump(image) async for image in bake_images
                ]
                return aiohttp.web.json_response(
                    data=response_payload, status=HTTPOk.status_code
                )

    @docs(tags=["service_accounts"], summary="Get service account by id or name")
    @response_schema(ServiceAccountSchema(), HTTPOk.status_code)
    async def get(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        username = await check_authorized(request)
        account = await self._resolve_service_account(request)
        # TODO: replace with proper permissions check when implemented
        if account.owner != username:
            id_or_name = request.match_info["id_or_name"]
            raise HTTPNotFound(text=f"Service account {id_or_name} not found")
        return aiohttp.web.json_response(
            data=ServiceAccountSchema().dump(account), status=HTTPOk.status_code
        )

    @docs(tags=["service_accounts"], summary="Revoke and delete service account")
    async def delete(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        username = await check_authorized(request)
        account = await self._resolve_service_account(request)
        # TODO: replace with proper permissions check when implemented
        if account.owner != username:
            id_or_name = request.match_info["id_or_name"]
            raise HTTPNotFound(text=f"Service account {id_or_name} not found")
        await self.service.delete(account.id)
        return aiohttp.web.Response(status=HTTPNoContent.status_code)

    @docs(
        tags=["service_accounts"],
        summary="Create new service account",
        responses={
            HTTPCreated.status_code: {
                "description": "Service account created",
                "schema": ServiceAccountWithTokenSchema(),
            },
            HTTPConflict.status_code: {
                "description": "Service Account with such name exists",
                "schema": ClientErrorSchema(),
            },
        },
    )
    @request_schema(ServiceAccountCreateSchema())
    async def create(
        self,
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        username = await check_authorized(request)
        schema = ServiceAccountCreateSchema()
        data_raw = schema.load(await request.json())
        data = AccountCreateData(
            **data_raw,
            owner=username,
        )
        try:
            account = await self.service.create(data)
        except ExistsError:
            return json_response(
                {
                    "code": "unique",
                    "description": "Service Account with such name exists",
                },
                status=HTTPConflict.status_code,
            )
        except NoAccessToRoleError:
            raise HTTPForbidden
        return aiohttp.web.json_response(
            data=ServiceAccountWithTokenSchema().dump(account),
            status=HTTPCreated.status_code,
        )


@middleware
async def handle_exceptions(
    request: Request, handler: Callable[[Request], Awaitable[StreamResponse]]
) -> StreamResponse:
    try:
        return await handler(request)
    except ValueError as e:
        payload = {"error": str(e)}
        return json_response(payload, status=HTTPBadRequest.status_code)
    except ValidationError as e:
        payload = {"error": str(e)}
        return json_response(payload, status=HTTPBadRequest.status_code)
    except aiohttp.web.HTTPException:
        raise
    except Exception as e:
        msg_str = f"Unexpected exception: {str(e)}. Path with query: {request.path_qs}."
        logging.exception(msg_str)
        payload = {"error": msg_str}
        return json_response(payload, status=HTTPInternalServerError.status_code)


async def create_api_v1_app() -> aiohttp.web.Application:
    api_v1_app = aiohttp.web.Application()
    api_v1_handler = ApiHandler()
    api_v1_handler.register(api_v1_app)
    return api_v1_app


async def create_service_accounts_app(config: Config) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    handler = ServiceAccountsApiHandler(app, config)
    handler.register(app)
    return app


@asynccontextmanager
async def create_auth_client(config: PlatformAuthConfig) -> AsyncIterator[AuthClient]:
    async with AuthClient(config.url, config.token) as client:
        yield client


def _setup_cors(app: aiohttp.web.Application, config: CORSConfig) -> None:
    if not config.allowed_origins:
        return

    logger.info(f"Setting up CORS with allowed origins: {config.allowed_origins}")
    default_options = aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
    cors = aiohttp_cors.setup(
        app, defaults={origin: default_options for origin in config.allowed_origins}
    )
    for route in app.router.routes():
        logger.debug(f"Setting up CORS for {route}")
        cors.add(route)


package_version = version(__package__)


async def add_version_to_header(request: Request, response: StreamResponse) -> None:
    response.headers["X-Service-Version"] = (
        f"platform-service-accounts-api/{package_version}"
    )


async def create_app(config: Config) -> aiohttp.web.Application:
    app = aiohttp.web.Application(middlewares=[handle_exceptions])
    app[CONFIG] = config

    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            logger.info("Initializing Auth client")
            auth_client = await exit_stack.enter_async_context(
                create_auth_client(config.platform_auth)
            )

            await setup_security(
                app=app, auth_client=auth_client, auth_scheme=AuthScheme.BEARER
            )

            logger.info("Initializing Postgres connection pool")
            postgres_pool = await exit_stack.enter_async_context(
                create_postgres_pool(config.postgres)
            )

            logger.info("Initializing PostgresStorage")
            storage: Storage = PostgresStorage(postgres_pool)

            logger.info("Initializing Service")
            app["service_accounts_app"]["service"] = AccountsService(
                auth_client=auth_client,
                storage=storage,
                api_base_url=config.api_base_url,
            )

            yield

    app.cleanup_ctx.append(_init_app)

    api_v1_app = await create_api_v1_app()
    app[API_V1_APP] = api_v1_app

    service_accounts_app = await create_service_accounts_app(config)
    app["service_accounts_app"] = service_accounts_app
    api_v1_app.add_subapp("/service_accounts", service_accounts_app)

    app.add_subapp("/api/v1", api_v1_app)

    app.on_response_prepare.append(add_version_to_header)

    _setup_cors(app, config.cors)
    if config.enable_docs:
        prefix = "/api/docs/v1/service_accounts"
        setup_aiohttp_apispec(
            app=app,
            title="Service Accounts API documentation",
            version="v1",
            url=f"{prefix}/swagger.json",
            static_path=f"{prefix}/static",
            swagger_path=f"{prefix}/ui",
            security=[{"jwt": []}],
            securityDefinitions={
                "jwt": {"type": "apiKey", "name": "Authorization", "in": "header"},
            },
        )
    return app


def setup_tracing(config: Config) -> None:
    if config.zipkin:
        setup_zipkin_tracer(
            config.zipkin.app_name,
            config.server.host,
            config.server.port,
            config.zipkin.url,
            config.zipkin.sample_rate,
        )

    if config.sentry:
        setup_sentry(
            config.sentry.dsn,
            app_name=config.sentry.app_name,
            cluster_name=config.sentry.cluster_name,
            sample_rate=config.sentry.sample_rate,
        )


def main() -> None:  # pragma: no coverage
    init_logging()
    config = EnvironConfigFactory().create()
    logging.info("Loaded config: %r", config)
    setup_tracing(config)
    aiohttp.web.run_app(
        create_app(config), host=config.server.host, port=config.server.port
    )
