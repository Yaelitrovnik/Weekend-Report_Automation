from __future__ import annotations

import copy
from typing import Any


def resolve_portainer_expected(config: dict[str, Any]) -> dict[str, Any]:
    portainer = config.get("portainer_expected", config)
    resolved = copy.deepcopy(portainer)
    common_services = portainer.get("services")
    defaults = portainer.get("defaults", {})
    sites = portainer.get("sites", {})
    if not isinstance(sites, dict):
        resolved["sites"] = {}
        return resolved
    if not isinstance(common_services, dict):
        return resolved
    resolved_sites: dict[str, Any] = {}
    for site_id, site_config in sites.items():
        if not isinstance(site_config, dict):
            continue
        site = copy.deepcopy(site_config)
        if site.get("service_inventory") == "common" or "services" not in site:
            service_overrides = _named_overrides(site.get("overrides", {}), "services")
            site["services"] = [
                _resolve_portainer_service(name, service, defaults, service_overrides.get(name, {}))
                for name, service in common_services.items()
                if isinstance(service, dict)
            ]
        else:
            site["services"] = [
                _normalize_portainer_service(service.get("name", str(idx)), service, defaults)
                for idx, service in enumerate(site.get("services", []))
                if isinstance(service, dict)
            ]
        resolved_sites[site_id] = site
    resolved["sites"] = resolved_sites
    return resolved


def resolve_rabbitmq_expected(config: dict[str, Any]) -> dict[str, Any]:
    rabbitmq = config.get("rabbitmq_expected", config)
    resolved = copy.deepcopy(rabbitmq)
    common_topology = rabbitmq.get("topology")
    defaults = rabbitmq.get("defaults", {})
    sites = rabbitmq.get("sites", {})
    if not isinstance(sites, dict):
        resolved["sites"] = {}
        return resolved
    if not isinstance(common_topology, dict):
        return resolved
    resolved_sites: dict[str, Any] = {}
    for site_id, site_config in sites.items():
        if not isinstance(site_config, dict):
            continue
        site = copy.deepcopy(site_config)
        if site.get("topology") == "common" or not _has_explicit_topology(site):
            overrides = site.get("overrides", {})
            site.update(_resolve_rabbitmq_topology(common_topology, defaults, overrides))
        resolved_sites[site_id] = site
    resolved["sites"] = resolved_sites
    return resolved


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _resolve_portainer_service(
    name: str,
    service: dict[str, Any],
    defaults: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    return _normalize_portainer_service(name, deep_merge(service, override), defaults)


def _normalize_portainer_service(
    name: str,
    service: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    merged = deep_merge(defaults, service)
    merged.setdefault("name", name)
    merged.setdefault("required", True)
    expected = copy.deepcopy(merged.get("expected", {}))
    for field in [
        "desired_replicas",
        "running_replicas",
        "healthy_replicas",
        "image",
        "image_comparison",
        "service_state",
        "task_state_policy",
    ]:
        if field in merged:
            expected[field] = merged.pop(field)
    if expected:
        merged["expected"] = expected
    return merged


def _has_explicit_topology(site: dict[str, Any]) -> bool:
    return any(key in site for key in ["vhosts", "queues", "exchanges", "bindings"])


def _resolve_rabbitmq_topology(
    common_topology: dict[str, Any],
    defaults: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "vhosts": _resolve_named_items(
            common_topology.get("vhosts", {}),
            defaults.get("vhost", {}),
            overrides.get("vhosts", {}),
        ),
        "queues": _resolve_named_items(
            common_topology.get("queues", {}),
            defaults.get("queue", {}),
            overrides.get("queues", {}),
        ),
        "exchanges": _resolve_named_items(
            common_topology.get("exchanges", {}),
            defaults.get("exchange", {}),
            overrides.get("exchanges", {}),
        ),
        "bindings": _resolve_named_items(
            common_topology.get("bindings", {}),
            defaults.get("binding", {}),
            overrides.get("bindings", {}),
        ),
    }


def _resolve_named_items(
    items: dict[str, Any],
    defaults: dict[str, Any],
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    resolved = []
    for name, item in items.items():
        if not isinstance(item, dict):
            continue
        merged = deep_merge(deep_merge(defaults, item), overrides.get(name, {}))
        merged.setdefault("name", name)
        merged.setdefault("required", True)
        resolved.append(merged)
    return resolved


def _named_overrides(overrides: Any, section: str) -> dict[str, Any]:
    if not isinstance(overrides, dict):
        return {}
    nested = overrides.get(section)
    if isinstance(nested, dict):
        return nested
    return overrides
