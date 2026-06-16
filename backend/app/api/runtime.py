from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.production import build_readiness_report
from app.core.security import require_admin
from app.core.version import BUILD_STAGE

router = APIRouter(tags=["runtime"])


@router.get("/livez")
async def livez() -> dict[str, object]:
    """Minimal process-liveness endpoint for container and platform probes."""

    return {"ok": True, "build": BUILD_STAGE}


@router.get("/readyz")
async def readyz(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Configuration readiness without secret or integration details."""

    report = build_readiness_report(settings)
    code = status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=report.public_payload())


@router.get("/v1/runtime/readiness", dependencies=[Depends(require_admin)])
async def detailed_readiness(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Authenticated, redacted production-readiness report."""

    return build_readiness_report(settings).detailed_payload()
