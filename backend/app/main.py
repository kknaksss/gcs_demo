from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (
    admin_users,
    approvals,
    auth,
    classification,
    document_tree,
    document_types,
    documents,
    drive_connector,
    explorer,
    health,
    organization,
    relation_candidates,
    search,
    webhooks,
)
from app.core.config import get_settings
from app.core.logging import setup_logging

setup_logging()
settings = get_settings()

app = FastAPI(title="cloud-file-organizer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(organization.router)
app.include_router(document_tree.router)
app.include_router(documents.router)
app.include_router(drive_connector.router)
app.include_router(classification.router)
app.include_router(approvals.router)
app.include_router(document_types.router)
app.include_router(relation_candidates.router)
app.include_router(explorer.router)
app.include_router(search.router)
app.include_router(webhooks.router)
