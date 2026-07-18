"""Versioned external-source connector contract and durable runner."""

from .sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRateLimited,
    ConnectorRecord,
    ConnectorRunError,
    ConnectorRunner,
    seed_acknowledged_records,
)
from .export_inbox import ExportInboxConnector, ExportInboxError
from .registry import (
    ConnectorAuth,
    ConnectorDefinition,
    ConnectorDefinitionV3,
    ConnectorPlacement,
    ConnectorPolicy,
    ConnectorRegistryError,
    ConnectorSync,
    REGISTRY,
)
from .kit import (
    CONNECTOR_KIT_API_VERSION,
    CONNECTOR_PAGE_WIRE_VERSION,
    decode_page_wire,
    encode_page_wire,
)
from .conformance import (
    CONNECTOR_CONFORMANCE_CELLS,
    CONNECTOR_CONFORMANCE_VERSION,
    ConformanceReport,
    render_conformance_report,
    run_connector_conformance,
)
from .remote_api import BoundedJsonRail, RemoteApiError, RemoteOperation
from .google_workspace import (
    GmailConnector,
    GoogleCalendarConnector,
    GoogleContactsConnector,
    GoogleDriveConnector,
)
from .work_apis import (
    GitHubActivityConnector,
    LinearActivityConnector,
    NotionWorkspaceConnector,
    SlackMessagesConnector,
    github_rail,
    linear_rail,
    notion_rail,
    slack_rail,
    x_rail,
)
from .x_activity import XActivityConnector
from .supervisor import (
    ConnectorSupervisor,
    ScheduleDefinition,
    ScheduledJob,
    SupervisorContractError,
    SupervisorStore,
)
from .host import ConnectorHostConfig, ConnectorHostError, build_host, load_host_config

__all__ = [
    "ConnectorContractError",
    "ConnectorPage",
    "ConnectorRateLimited",
    "ConnectorRecord",
    "ConnectorRunError",
    "ConnectorRunner",
    "seed_acknowledged_records",
    "ExportInboxConnector",
    "ExportInboxError",
    "ConnectorDefinition",
    "ConnectorDefinitionV3",
    "ConnectorPlacement",
    "ConnectorAuth",
    "ConnectorSync",
    "ConnectorPolicy",
    "ConnectorRegistryError",
    "REGISTRY",
    "CONNECTOR_KIT_API_VERSION",
    "CONNECTOR_PAGE_WIRE_VERSION",
    "encode_page_wire",
    "decode_page_wire",
    "CONNECTOR_CONFORMANCE_CELLS",
    "CONNECTOR_CONFORMANCE_VERSION",
    "ConformanceReport",
    "render_conformance_report",
    "run_connector_conformance",
    "BoundedJsonRail",
    "RemoteApiError",
    "RemoteOperation",
    "GmailConnector",
    "GoogleCalendarConnector",
    "GoogleContactsConnector",
    "GoogleDriveConnector",
    "GitHubActivityConnector",
    "LinearActivityConnector",
    "NotionWorkspaceConnector",
    "SlackMessagesConnector",
    "github_rail",
    "linear_rail",
    "notion_rail",
    "slack_rail",
    "x_rail",
    "XActivityConnector",
    "ConnectorSupervisor",
    "ScheduleDefinition",
    "ScheduledJob",
    "SupervisorContractError",
    "SupervisorStore",
    "ConnectorHostConfig",
    "ConnectorHostError",
    "build_host",
    "load_host_config",
]
