from __future__ import annotations

import functools
import re
from collections.abc import Callable
from typing import Any, TypeVar

import aiohttp.web
from aiohttp_apispec import querystring_schema
from marshmallow import Schema, ValidationError, fields

F = TypeVar("F", bound=Callable[..., Any])


def query_schema(**kwargs: fields.Field) -> Callable[[F], F]:
    schema: Schema = Schema.from_dict(kwargs)()  # type: ignore

    def _decorator(handler: F) -> F:
        @querystring_schema(schema)
        @functools.wraps(handler)
        async def _wrapped(self: Any, request: aiohttp.web.Request) -> Any:
            query_data = {
                key: (
                    request.query.getall(key)
                    if len(request.query.getall(key)) > 1
                    or isinstance(schema.fields.get(key), fields.List)
                    else request.query[key]
                )
                for key in request.query.keys()
            }
            validated = schema.load(query_data)
            return await handler(self, request, **validated)

        return _wrapped

    return _decorator


NAME_PATTERN = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*"
)


def validate_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise ValidationError("Invalid service account name")


class ServiceAccountCreateSchema(Schema):
    name = fields.String(required=False, load_default=None, validate=validate_name)
    default_cluster = fields.String(required=True)
    default_project = fields.String(required=True)
    default_org = fields.String(required=False)


class ServiceAccountSchema(ServiceAccountCreateSchema):
    id = fields.String(required=True)
    role = fields.String(required=True)
    owner = fields.String(required=True)
    created_at = fields.AwareDateTime(required=True)
    role_deleted = fields.Boolean(required=True, dump_default=False)


class ServiceAccountWithTokenSchema(ServiceAccountSchema):
    token = fields.String(required=True)


class ClientErrorSchema(Schema):
    code = fields.String(required=True)
    description = fields.String(required=True)
