from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
)
from pydantic_core import ErrorDetails

Placeholder = Literal["<TBD>", "<TO_VERIFY>", "<NOT_APPLICABLE>"]
StrictNumber = StrictInt | StrictFloat | Placeholder
StrictBoolean = StrictBool | Placeholder
StrictString = StrictStr | Placeholder


class ExpectedStateModel(BaseModel):
    """Base model for expected-state documents.

    Extra fields remain permitted so an owner can add evidence-only details without
    silently discarding them. Known expected-state fields are strictly typed.
    """

    model_config = ConfigDict(extra="allow", strict=True)


class ExpectedService(ExpectedStateModel):
    desired_replicas: StrictNumber | None = None
    running_replicas: StrictNumber | None = None
    healthy_replicas: StrictNumber | None = None
    service_state: StrictString | None = None
    task_state_policy: dict[StrictString, StrictString] | Placeholder | None = None
    image: StrictString | dict[str, Any] | None = None
    image_comparison: StrictString | None = None


class PortainerService(ExpectedStateModel):
    name: StrictString | None = None
    required: StrictBoolean | None = None
    expected: ExpectedService | Placeholder | None = None


class PortainerSite(ExpectedStateModel):
    environment_type: StrictString | None = None
    service_inventory: StrictString | None = None
    services: list[PortainerService] | Placeholder | None = None
    overrides: dict[str, Any] | Placeholder | None = None
    connection: dict[str, Any] | Placeholder | None = None


class PortainerExpected(ExpectedStateModel):
    collection_mode: StrictString | None = None
    defaults: dict[str, Any] | Placeholder | None = None
    services: dict[str, PortainerService] | Placeholder | None = None
    sites: dict[str, PortainerSite] | Placeholder | None = None
    fixture_actual: dict[str, Any] | None = None


class RabbitMQNamedItem(ExpectedStateModel):
    name: StrictString | None = None
    required: StrictBoolean | None = None


class RabbitMQQueue(RabbitMQNamedItem):
    vhost: StrictString | None = None
    durable: StrictBoolean | None = None
    auto_delete: StrictBoolean | None = None
    exclusive: StrictBoolean | None = None
    min_consumers: StrictNumber | None = None
    warning_messages: StrictNumber | None = None
    critical_messages: StrictNumber | None = None


class RabbitMQExchange(RabbitMQNamedItem):
    vhost: StrictString | None = None
    type: StrictString | None = None
    durable: StrictBoolean | None = None
    auto_delete: StrictBoolean | None = None


class RabbitMQBinding(RabbitMQNamedItem):
    vhost: StrictString | None = None
    source: StrictString | None = None
    destination: StrictString | None = None
    destination_type: StrictString | None = None
    routing_key: StrictString | None = None


class RabbitMQTopology(ExpectedStateModel):
    vhosts: dict[str, RabbitMQNamedItem] | Placeholder | None = None
    queues: dict[str, RabbitMQQueue] | Placeholder | None = None
    exchanges: dict[str, RabbitMQExchange] | Placeholder | None = None
    bindings: dict[str, RabbitMQBinding] | Placeholder | None = None


class RabbitMQSite(ExpectedStateModel):
    topology: StrictString | None = None
    overrides: dict[str, Any] | Placeholder | None = None
    vhosts: list[RabbitMQNamedItem] | Placeholder | None = None
    queues: list[RabbitMQQueue] | Placeholder | None = None
    exchanges: list[RabbitMQExchange] | Placeholder | None = None
    bindings: list[RabbitMQBinding] | Placeholder | None = None


class RabbitMQExpected(ExpectedStateModel):
    collection_mode: StrictString | None = None
    defaults: dict[str, Any] | Placeholder | None = None
    topology: RabbitMQTopology | Placeholder | None = None
    sites: dict[str, RabbitMQSite] | Placeholder | None = None
    connections: dict[str, Any] | Placeholder | None = None
    fixture_actual: dict[str, Any] | None = None


class Threshold(ExpectedStateModel):
    warning_percent: StrictNumber | None = None
    critical_percent: StrictNumber | None = None


class Server(ExpectedStateModel):
    id: StrictString | None = None
    hostname: StrictString | None = None
    required: StrictBoolean | None = None
    ssh_port: StrictNumber | None = None
    filesystems: list[Threshold] | Placeholder | None = None
    nfs_mounts: list[Threshold] | Placeholder | None = None


class ServerSite(ExpectedStateModel):
    servers: list[Server] | Placeholder | None = None


class ServersExpected(ExpectedStateModel):
    sites: dict[str, ServerSite] | Placeholder | None = None


class DatabaseExpected(ExpectedStateModel):
    collection_mode: StrictString | None = None
    adapter: StrictString | None = None
    function_reference: StrictString | None = None
    required_secret_env: list[StrictString] | Placeholder | None = None
    fixture_actual: dict[str, Any] | None = None


class ExpectedStateConfig(ExpectedStateModel):
    portainer_expected: PortainerExpected | None = None
    rabbitmq_expected: RabbitMQExpected | None = None
    servers: ServersExpected | None = None
    database: DatabaseExpected | None = None


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    path: str
    message: str


def validate_expected_state_schema(config: dict[str, Any]) -> list[SchemaIssue]:
    """Return structural errors without changing legacy semantic validation messages."""

    try:
        ExpectedStateConfig.model_validate(config)
    except ValidationError as exc:
        issues: dict[str, SchemaIssue] = {}
        for error in exc.errors():
            if any(
                isinstance(part, str) and part.startswith("literal[")
                for part in error["loc"]
            ):
                continue
            issue = _schema_issue(error)
            issues.setdefault(issue.path, issue)
        return list(issues.values())
    return []


def _schema_issue(error: ErrorDetails) -> SchemaIssue:
    location = _format_location(error["loc"])
    error_type = str(error["type"])
    if error_type in {"dict_type", "model_type"}:
        message = "must be an object"
    elif error_type == "list_type":
        message = "must be a list"
    elif error_type in {"int_type", "float_type"}:
        message = "must be a number"
    elif error_type == "bool_type":
        message = "must be boolean"
    elif error_type == "string_type":
        message = "must be a string"
    else:
        message = str(error["msg"])
    return SchemaIssue(location, message)


def _format_location(location: tuple[Any, ...]) -> str:
    location = tuple(
        part
        for part in location
        if not (
            isinstance(part, str)
            and (
                part in {"bool", "float", "int", "str"}
                or part.startswith("dict[")
                or part.startswith("list[")
            )
        )
    )
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        elif path:
            path += f".{part}"
        else:
            path = str(part)
    return path
