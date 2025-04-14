import re

from marshmallow import Schema, ValidationError, fields

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
