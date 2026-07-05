from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.ai_council import router as ai_council_router
from app.api.benchmark import router as benchmark_router
from app.api.chat import router as chat_router
from app.api.db import router as db_router
from app.api.ecosystem import router as ecosystem_router
from app.api.execution import router as execution_router
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.model_registry import router as model_registry_router
from app.api.models import router as models_router
from app.api.ops_events import router as ops_events_router
from app.api.providers import router as providers_router
from app.api.repositories import router as repositories_router
from app.api.repository_memory import router as repository_memory_router
from app.api.runtime import router as runtime_router
from app.api.skills import router as skills_router
from app.api.system import router as system_router
from app.api.vectorize import router as vectorize_router
from app.api.workflow_graphs import router as workflow_graphs_router
from app.api.workflows import router as workflows_router
from app.core.config import Settings, get_settings
from app.core.middleware import ProductionMiddleware
from app.core.production import enforce_production_readiness
from app.services import model_registry
from app.storage.sql_store import SqlStore

logger = logging.getLogger("uvicorn.error.hive.startup")


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        report = enforce_production_readiness(active_settings)
        logger.info(
            "HIVE startup ready=%s environment=%s app_version=%s warnings=%s",
            report.ready,
            report.environment,
            report.app_version,
            len(report.warnings),
        )
        seeded_count = model_registry.seed_from_json(active_settings.model_registry_seed_json)
        if seeded_count:
            logger.info("HIVE Model Registry seeded entries=%s", seeded_count)
        if active_settings.database_enabled and active_settings.database_auto_init:
            schema_result = SqlStore(active_settings).init_schema()
            logger.info(
                "HIVE SQL schema auto-init ok=%s enabled=%s dialect=%s error=%s",
                schema_result.get("ok"),
                schema_result.get("enabled"),
                schema_result.get("dialect"),
                schema_result.get("error"),
            )
            if not schema_result.get("ok") and active_settings.production_require_database:
                raise RuntimeError(f"HIVE SQL schema auto-init failed: {schema_result.get('error')}")
        yield
        logger.info("HIVE shutdown complete")

    docs_url = "/docs" if active_settings.expose_api_docs else None
    redoc_url = "/redoc" if active_settings.expose_api_docs else None
    openapi_url = "/openapi.json" if active_settings.expose_api_docs else None

    application = FastAPI(
        title=active_settings.app_name,
        version=active_settings.app_version,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    application.dependency_overrides[get_settings] = lambda: active_settings

    @application.exception_handler(Exception)
    async def unhandled_exception(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unavailable")
        logger.exception(
            "Unhandled request failure request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
            exc_info=error,
        )
        headers = {
            "X-Request-ID": request_id,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        }
        if not active_settings.is_dev:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
            headers=headers,
        )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Cache-Control", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    if active_settings.trusted_hosts_enabled and "*" not in active_settings.effective_allowed_hosts:
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=active_settings.effective_allowed_hosts,
        )

    # Added last so it wraps CORS and TrustedHost responses as well as route responses.
    application.add_middleware(ProductionMiddleware, settings=active_settings)

    application.include_router(health_router)
    application.include_router(runtime_router)
    application.include_router(models_router, prefix="/v1")
    application.include_router(chat_router, prefix="/v1")
    application.include_router(files_router, prefix="/v1")
    application.include_router(db_router, prefix="/v1")
    application.include_router(vectorize_router, prefix="/v1")
    application.include_router(workflows_router, prefix="/v1")
    application.include_router(workflow_graphs_router, prefix="/v1")
    application.include_router(ecosystem_router, prefix="/v1")
    application.include_router(skills_router, prefix="/v1")
    application.include_router(execution_router, prefix="/v1")
    application.include_router(system_router, prefix="/v1")
    application.include_router(ops_events_router, prefix="/v1")
    application.include_router(repositories_router, prefix="/v1")
    application.include_router(repository_memory_router, prefix="/v1")
    application.include_router(model_registry_router, prefix="/v1")
    application.include_router(providers_router, prefix="/v1")
    application.include_router(ai_council_router, prefix="/v1")
    application.include_router(benchmark_router, prefix="/v1")
    return application


app = create_app()
