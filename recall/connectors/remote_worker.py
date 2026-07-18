"""Remote-only validation and aggregate preview for the connector host."""

from __future__ import annotations

from connectors.host import (
    ConnectorHostConfig,
    ConnectorHostError,
    RemoteOptions,
    load_host_config,
    preview_host_config,
    run_host_daemon,
    run_host_once,
)
from connectors.registry import REGISTRY, ConnectorDefinitionV3


REMOTE_WORKER_CONNECTORS = tuple(
    item.connector_id
    for item in REGISTRY
    if (
        isinstance(item, ConnectorDefinitionV3)
        and item.mode == "pull"
        and item.execution_placement == "remote_worker"
    )
)


def validate_remote_worker_config(config: ConnectorHostConfig) -> None:
    if not isinstance(config, ConnectorHostConfig):
        raise ConnectorHostError("invalid_config")
    if any(
        job.schedule.connector_id not in REMOTE_WORKER_CONNECTORS
        or not isinstance(job.connector, RemoteOptions)
        for job in config.jobs
    ):
        raise ConnectorHostError("non_remote_connector")


def preview_remote_worker_config(config: ConnectorHostConfig) -> dict:
    validate_remote_worker_config(config)
    preview = preview_host_config(config)
    return {**preview, "profile": "remote_worker"}


def load_remote_worker_config(path):
    config = load_host_config(path)
    validate_remote_worker_config(config)
    return config


def run_remote_worker_once(config_path, state_path):
    return run_host_once(
        config_path,
        state_path,
        config_loader=load_remote_worker_config,
    )


def run_remote_worker_daemon(config_path, state_path):
    return run_host_daemon(
        config_path,
        state_path,
        config_loader=load_remote_worker_config,
    )


__all__ = [
    "REMOTE_WORKER_CONNECTORS",
    "preview_remote_worker_config",
    "load_remote_worker_config",
    "run_remote_worker_daemon",
    "run_remote_worker_once",
    "validate_remote_worker_config",
]
