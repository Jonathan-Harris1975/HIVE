from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.files import router as files_router
from app.api.db import router as db_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.vectorize import router as vectorize_router
from app.api.workflows import router as workflows_router
from app.api.ecosystem import router as ecosystem_router
from app.api.skills import router as skills_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(models_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
app.include_router(files_router, prefix="/v1")
app.include_router(db_router, prefix="/v1")
app.include_router(vectorize_router, prefix="/v1")
app.include_router(workflows_router, prefix="/v1")
app.include_router(ecosystem_router, prefix="/v1")
app.include_router(skills_router, prefix="/v1")
