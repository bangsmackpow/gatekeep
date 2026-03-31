from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.config import settings
from src.models.database import init_db, engine, async_session_factory
from src.middleware.audit import AuditMiddleware
from src.api.documents import router as documents_router
from src.api.search import router as search_router
from src.api.upload import router as upload_router
from src.api.audit import router as audit_router
from src.api.auth import router as auth_router
from src.api.matters import router as matters_router
from src.api.matter_import_export import router as matter_import_export_router
from elasticsearch import AsyncElasticsearch


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.es = AsyncElasticsearch(
        settings.ELASTICSEARCH_URL,
        basic_auth=(settings.ELASTICSEARCH_USER, settings.ELASTICSEARCH_PASSWORD),
        verify_certs=False,
        request_timeout=30,
    )
    await app.state.es.ping()
    await ensure_elastic_indices(app.state.es)
    yield
    await app.state.es.close()
    await engine.dispose()


async def ensure_elastic_indices(es: AsyncElasticsearch):
    indices = {
        "documents": {
            "mappings": {
                "properties": {
                    "document_id": {"type": "keyword"},
                    "original_filename": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "author": {"type": "text", "analyzer": "standard", "fields": {"keyword": {"type": "keyword"}}},
                    "email_subject": {"type": "text", "analyzer": "standard"},
                    "sender_email": {"type": "keyword"},
                    "sender_name": {"type": "text", "analyzer": "standard"},
                    "recipient_emails": {"type": "keyword"},
                    "subject": {"type": "text", "analyzer": "standard"},
                    "title": {"type": "text", "analyzer": "standard"},
                    "extracted_text": {"type": "text", "analyzer": "standard"},
                    "file_extension": {"type": "keyword"},
                    "mime_type": {"type": "keyword"},
                    "matter_id": {"type": "keyword"},
                    "sha256_hash": {"type": "keyword"},
                    "sent_date": {"type": "date"},
                    "received_date": {"type": "date"},
                    "created_date": {"type": "date"},
                    "uploaded_at": {"type": "date"},
                    "ocr_text": {"type": "text", "analyzer": "standard"},
                    "language": {"type": "keyword"},
                }
            },
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        }
    }
    for index_name, config in indices.items():
        if not await es.indices.exists(index=index_name):
            await es.indices.create(index=index_name, **config)


app = FastAPI(
    title="GateKeep Legal Discovery",
    description="Secure legal document discovery and management system",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(AuditMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(matters_router, prefix="/api", tags=["Matters"])
app.include_router(matter_import_export_router, prefix="/api", tags=["Matter Import/Export"])
app.include_router(upload_router, prefix="/api", tags=["Upload"])
app.include_router(documents_router, prefix="/api", tags=["Documents"])
app.include_router(search_router, prefix="/api", tags=["Search"])
app.include_router(audit_router, prefix="/api", tags=["Audit"])

app.mount("/static", StaticFiles(directory="src/frontend/static"), name="static")


@app.get("/health")
async def health():
    return {"status": "healthy", "environment": settings.ENVIRONMENT}


@app.get("/")
async def index():
    from fastapi.responses import HTMLResponse
    from pathlib import Path
    template = Path("src/frontend/templates/index.html")
    if template.exists():
        return HTMLResponse(content=template.read_text())
    return {"message": "GateKeep Legal Discovery System", "docs": "/api/docs"}
