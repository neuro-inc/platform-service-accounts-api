from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import aiohttp
import aiohttp.web
import aiohttp_cors
from aiohttp.web import (
    HTTPBadRequest,
    HTTPInternalServerError,
    Request,
    Response,
    StreamResponse,
    delete,
    get,
    json_response,
    middleware,
    post,
)
from aiohttp.web_exceptions import (
    HTTPConflict,
    HTTPCreated,
    HTTPForbidden,
    HTTPNoContent,
    HTTPNotFound,
    HTTPOk,
)
from aiohttp_security import check_authorized
from aiohttp_swagger3 import SwaggerDocs, SwaggerInfo, SwaggerUiSettings
from marshmallow import ValidationError
from neuro_auth_client import AuthClient, User
from neuro_auth_client.security import AuthScheme, setup_security
from neuro_logging import init_logging, notrace, setup_sentry

from platform_service_accounts_api import __version__

from .config import Config, CORSConfig, PlatformAuthConfig
from .config_factory import EnvironConfigFactory
from .identity import untrusted_user
from .postgres import create_postgres_pool
from .schema import (
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
SERVICE: aiohttp.web.AppKey[AccountsService] = aiohttp.web.AppKey(
    "SERVICE", AccountsService
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
        payload = {"error": msg_str}
        logging.exception(msg_str)
        return json_response(payload, status=HTTPInternalServerError.status_code)


class ApiHandler:
    def register(self, app: aiohttp.web.Application) -> None:
        app.router.add_get("/ping", self.handle_ping)
        app.router.add_get("/secured-ping", self.handle_secured_ping)

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
        app.router.add_get("", self.list)
        app.router.add_post("", self.create)
        app.router.add_get("/{id_or_name}", self.get)
        app.router.add_delete("/{id_or_name}", self.delete)

    @property
    def service(self) -> AccountsService:
        return self._app[SERVICE]

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

    async def list(self, request: Request) -> aiohttp.web.StreamResponse:
        """
        ---
        summary: List all Service Accounts current user has access
        security:
          - jwt: []
        tags:
          - Service Accounts
        responses:
          '200':
            description: List of service accounts
            content:
              application/json:
                schema:
                  type: array
                  items:
                    $ref: '#/components/schemas/ServiceAccount'
        """
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
            response_payload = [
                ServiceAccountSchema().dump(image) async for image in bake_images
            ]
            return aiohttp.web.json_response(
                data=response_payload, status=HTTPOk.status_code
            )

    async def get(self, request: Request) -> Response:
        """
        ---
        summary: Get a Service Account by id or name
        security:
          - jwt: []
        tags:
          - Service Accounts
        parameters:
          - name: id_or_name
            in: path
            required: true
            schema:
              type: string
        responses:
          '200':
            description: Found service account
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/ServiceAccount'
          '404':
            description: Not found
        """
        username = await check_authorized(request)
        account = await self._resolve_service_account(request)
        # TODO: replace with proper permissions check when implemented
        if account.owner != username:
            id_or_name = request.match_info["id_or_name"]
            raise HTTPNotFound(text=f"Service account {id_or_name} not found")
        return aiohttp.web.json_response(
            data=ServiceAccountSchema().dump(account), status=HTTPOk.status_code
        )

    async def create(self, request: Request) -> Response:
        """
        ---
        summary: Create a new Service Account
        security:
          - jwt: []
        tags:
          - Service Accounts
        requestBody:
          required: true
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ServiceAccountCreate'
        responses:
          '201':
            description: Created
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/ServiceAccountWithToken'
          '409':
            description: Conflict
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/ClientError'
        """
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

    async def delete(self, request: Request) -> Response:
        """
        ---
        summary: Delete a Service Account
        security:
          - jwt: []
        tags:
          - Service Accounts
        parameters:
          - name: id_or_name
            in: path
            required: true
            schema:
              type: string
        responses:
          '204':
            description: Deleted
        """
        username = await check_authorized(request)
        account = await self._resolve_service_account(request)
        if account.owner != username:
            raise HTTPNotFound(
                text=f"Service account {request.match_info['id_or_name']} not found"
            )
        await self.service.delete(account.id)
        return Response(status=HTTPNoContent.status_code)


@asynccontextmanager
async def create_auth_client(config: PlatformAuthConfig) -> AsyncIterator[AuthClient]:
    async with AuthClient(config.url, config.token) as client:
        yield client


def _setup_cors(app: aiohttp.web.Application, config: CORSConfig) -> None:
    if not config.allowed_origins:
        return

    logger.info("Setting up CORS with allowed origins: %s", config.allowed_origins)
    default_options = aiohttp_cors.ResourceOptions(
        allow_credentials=True, expose_headers="*", allow_headers="*"
    )
    cors = aiohttp_cors.setup(
        app, defaults=dict.fromkeys(config.allowed_origins, default_options)
    )
    for route in app.router.routes():
        logger.debug("Setting up CORS for %s", route)
        cors.add(route)


async def add_version_to_header(request: Request, response: StreamResponse) -> None:
    response.headers["X-Service-Version"] = (
        f"platform-service-accounts-api/{__version__}"
    )


async def create_api_v1_app() -> aiohttp.web.Application:
    api_v1_app = aiohttp.web.Application()
    api_v1_handler = ApiHandler()
    api_v1_handler.register(api_v1_app)
    return api_v1_app


def create_service_accounts_subapp(config: Config) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    handler = ServiceAccountsApiHandler(app, config)
    handler.register(app)
    return app


async def create_app(config: Config) -> aiohttp.web.Application:
    app = aiohttp.web.Application(middlewares=[handle_exceptions])
    app[CONFIG] = config

    service_accounts_app = create_service_accounts_subapp(config)
    app.add_subapp("/api/v1/service_accounts", service_accounts_app)

    api_v1_app = await create_api_v1_app()
    app.add_subapp("/api/v1", api_v1_app)

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
            service = AccountsService(
                auth_client=auth_client,
                storage=storage,
                api_base_url=config.api_base_url,
            )
            app[SERVICE] = service
            # Propagate the service instance to the subapp.
            service_accounts_app[SERVICE] = service
            yield

    app.cleanup_ctx.append(_init_app)

    async def handle_ping(request: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.Response(text="Pong")

    app.router.add_get("/ping", handle_ping)

    app.on_response_prepare.append(add_version_to_header)

    _setup_cors(app, config.cors)
    if config.enable_docs:
        prefix = "/api/docs/v1/service_accounts"
        docs = SwaggerDocs(
            app,
            info=SwaggerInfo(
                title="Service Accounts API documentation",
                version="v1",
                description="API to manage service accounts",
            ),
            swagger_ui_settings=SwaggerUiSettings(path=f"{prefix}/ui"),
        )
        docs.spec["components"] = {
            "securitySchemes": {
                "jwt": {
                    "type": "apiKey",
                    "name": "Authorization",
                    "in": "header",
                }
            },
            "schemas": {
                "ServiceAccount": {
                    "type": "object",
                    "required": [
                        "id",
                        "name",
                        "role",
                        "owner",
                        "created_at",
                        "role_deleted",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "default_cluster": {"type": "string"},
                        "default_project": {"type": "string"},
                        "default_org": {"type": "string"},
                        "role": {"type": "string"},
                        "owner": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "role_deleted": {"type": "boolean"},
                    },
                },
                "ServiceAccountCreate": {
                    "type": "object",
                    "required": ["default_cluster", "default_project"],
                    "properties": {
                        "name": {"type": "string"},
                        "default_cluster": {"type": "string"},
                        "default_project": {"type": "string"},
                        "default_org": {"type": "string"},
                    },
                },
                "ServiceAccountWithToken": {
                    "allOf": [
                        {"$ref": "#/components/schemas/ServiceAccount"},
                        {
                            "type": "object",
                            "required": ["token"],
                            "properties": {"token": {"type": "string"}},
                        },
                    ]
                },
                "ClientError": {
                    "type": "object",
                    "required": ["code", "description"],
                    "properties": {
                        "code": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
        }
        handler_instance = ServiceAccountsApiHandler(service_accounts_app, config)
        docs.add_routes(
            [
                get("/api/v1/service_accounts", handler_instance.list),
                post("/api/v1/service_accounts", handler_instance.create),
                get("/api/v1/service_accounts/{id_or_name}", handler_instance.get),
                delete(
                    "/api/v1/service_accounts/{id_or_name}", handler_instance.delete
                ),
            ]
        )
    return app


def main() -> None:  # pragma: no coverage
    init_logging()
    config = EnvironConfigFactory().create()
    logging.info("Loaded config: %r", config)
    setup_sentry()
    aiohttp.web.run_app(
        create_app(config), host=config.server.host, port=config.server.port
    )
