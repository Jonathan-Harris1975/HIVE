from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.production import build_readiness_report
from app.core.security import require_admin
from app.core.version import BUILD_STAGE
from app.services.dependency_readiness import build_dependency_readiness_report

router = APIRouter(tags=["runtime"])


@router.get("/livez")
async def livez() -> dict[str, object]:
    """Minimal process-liveness endpoint for container and platform probes."""

    return {"ok": True, "build": BUILD_STAGE}


@router.get("/readyz")
async def readyz(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Configuration readiness without secret or integration details."""

    configuration = build_readiness_report(settings)
    report = await run_in_threadpool(build_dependency_readiness_report, settings)
    code = status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=code,
        content=report.public_payload(
            environment=configuration.environment,
            app_version=configuration.app_version,
        ),
    )


@router.get("/v1/runtime/readiness", dependencies=[Depends(require_admin)])
async def detailed_readiness(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Authenticated, redacted production-readiness report."""

    configuration = build_readiness_report(settings)
    dependencies = await run_in_threadpool(build_dependency_readiness_report, settings)
    payload = configuration.detailed_payload()
    payload.update(
        dependencies.detailed_payload(
            environment=configuration.environment,
            app_version=configuration.app_version,
        )
    )
    return payload
