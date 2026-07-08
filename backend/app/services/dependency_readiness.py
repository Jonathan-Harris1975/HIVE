from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import Settings
from app.core.production import build_readiness_report
from app.services.skill_registry import SEARCH_DOCUMENTS_KEY, SHARED_MANIFEST_KEY
from app.storage.r2 import R2Storage

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"key": None, "expires_at": 0.0, "report": None}


@dataclass(frozen=True)
class DependencyProbe:
    name: str
    status: str
    message: str
    required: bool = True


@dataclass(frozen=True)
class DependencyReadinessReport:
    ready: bool
    configuration_ready: bool
    probes_enabled: bool
    cached: bool
    probes: tuple[DependencyProbe, ...]

    def public_payload(self, *, environment: str, app_version: str) -> dict[str, object]:
        errors = [probe for probe in self.probes if probe.status == "error"]
        degraded = [probe for probe in self.probes if probe.status == "warning"]
        return {
            "ready": self.ready,
            "environment": environment,
            "app_version": app_version,
            "configuration_ready": self.configuration_ready,
            "dependency_probes_enabled": self.probes_enabled,
            "dependency_error_count": len(errors),
            "dependency_warning_count": len(degraded),
            "dependency_probe_count": len(self.probes),
        }

    def detailed_payload(self, *, environment: str, app_version: str) -> dict[str, object]:
        payload = self.public_payload(environment=environment, app_version=app_version)
        payload["cached"] = self.cached
        payload["dependency_probes"] = [asdict(probe) for probe in self.probes]
        return payload


def build_dependency_readiness_report(
    settings: Settings,
    *,
    force: bool = False,
) -> DependencyReadinessReport:
    """Run bounded, read-only production dependency probes with a short cache."""

    configuration = build_readiness_report(settings)
    if not settings.readiness_dependency_probes_enabled or settings.is_dev:
        return DependencyReadinessReport(
            ready=configuration.ready,
            configuration_ready=configuration.ready,
            probes_enabled=False,
            cached=False,
            probes=(),
        )

    cache_key = _cache_key(settings)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get("report")
        if (
            not force
            and _CACHE.get("key") == cache_key
            and float(_CACHE.get("expires_at") or 0) > now
            and isinstance(cached, DependencyReadinessReport)
        ):
            return DependencyReadinessReport(
                ready=cached.ready,
                configuration_ready=cached.configuration_ready,
                probes_enabled=cached.probes_enabled,
                cached=True,
                probes=cached.probes,
            )

    probes = _probe_required_r2_lanes(settings)
    required_errors = [probe for probe in probes if probe.required and probe.status == "error"]
    report = DependencyReadinessReport(
        ready=configuration.ready and not required_errors,
        configuration_ready=configuration.ready,
        probes_enabled=True,
        cached=False,
        probes=tuple(probes),
    )
    with _CACHE_LOCK:
        _CACHE["key"] = cache_key
        _CACHE["expires_at"] = now + max(1, settings.readiness_dependency_probe_cache_seconds)
        _CACHE["report"] = report
    return report


def clear_dependency_readiness_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update({"key": None, "expires_at": 0.0, "report": None})


def _probe_required_r2_lanes(settings: Settings) -> list[DependencyProbe]:
    required = settings.required_r2_read_lane_names
    if not required:
        return []
    storage = R2Storage(settings)
    probes: list[DependencyProbe] = []
    for lane_name in required:
        # required_r2_read_lane_names is entirely operator-configured, never
        # user input, so it is safe to resolve hidden lanes (e.g. meta_system).
        lane = settings.internal_r2_lane(lane_name)
        if not lane or not lane.get("bucket"):
            probes.append(
                DependencyProbe(
                    name=f"r2_lane:{lane_name}",
                    status="error",
                    message="Required lane has no configured bucket.",
                )
            )
            continue
        read_only = not bool(lane.get("writable"))
        try:
            storage.list_objects_page(
                prefix="",
                limit=1,
                bucket=str(lane["bucket"]),
                public_base_url=lane.get("public_base_url"),
                delimiter=None,
                read_only=read_only,
                max_scan_keys=1,
            )
            probes.append(
                DependencyProbe(
                    name=f"r2_lane:{lane_name}",
                    status="ok",
                    message="A bounded object-list probe succeeded.",
                )
            )
        except (RuntimeError, ValueError, OSError):
            probes.append(
                DependencyProbe(
                    name=f"r2_lane:{lane_name}",
                    status="error",
                    message="The bounded object-list probe failed.",
                )
            )
            continue

        if lane_name == "hive_skills":
            probes.extend(_probe_governed_skill_objects(settings, storage, lane))
    return probes


def _probe_governed_skill_objects(
    settings: Settings,
    storage: R2Storage,
    lane: dict[str, Any],
) -> list[DependencyProbe]:
    probes: list[DependencyProbe] = []
    for key, expected_field in (
        (SHARED_MANIFEST_KEY, None),
        (SEARCH_DOCUMENTS_KEY, "documents"),
    ):
        try:
            obj = storage.read_object(
                key,
                max_bytes=settings.skill_registry_max_source_bytes,
                bucket=str(lane["bucket"]),
                public_base_url=lane.get("public_base_url"),
                read_only=True,
            )
            payload = json.loads(obj.content.decode("utf-8"))
            if expected_field and not (
                isinstance(payload, list)
                or (isinstance(payload, dict) and isinstance(payload.get(expected_field), list))
            ):
                raise ValueError("Unexpected governed skills object schema")
            if not expected_field and not isinstance(payload, (dict, list)):
                raise ValueError("Unexpected governed manifest schema")
            probes.append(
                DependencyProbe(
                    name=f"hive_skills_object:{key}",
                    status="ok",
                    message="The governed JSON object was read and schema-checked.",
                )
            )
        except (RuntimeError, ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            probes.append(
                DependencyProbe(
                    name=f"hive_skills_object:{key}",
                    status="error",
                    message="The governed JSON object could not be read and schema-checked.",
                )
            )
    return probes


def _cache_key(settings: Settings) -> tuple[object, ...]:
    lanes = tuple(
        (name, (settings.internal_r2_lane(name) or {}).get("bucket"))
        for name in settings.required_r2_read_lane_names
    )
    return (
        settings.app_env,
        settings.r2_endpoint_url,
        settings.r2_multi_bucket_read_enabled,
        lanes,
    )
