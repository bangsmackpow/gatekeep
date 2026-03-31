# GateKeep - Legal Discovery & Document Management System (The Walled Garden)

A self-hosted, containerized legal discovery platform designed for Azure environments. Provides secure forensic document ingestion, OCR processing, full-text search, and immutable audit trails.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              AZURE ENVIRONMENT                                   │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                        VNET (Private Network)                           │    │
│  │                                                                          │    │
│  │  ┌──────────────┐    ┌──────────────────────────────────────────────┐   │    │
│  │  │  Azure LB /  │───▶│              Traefik Reverse Proxy            │   │    │
│  │  │  App Gateway │    │         (SSL/TLS Termination)                 │   │    │
│  │  └──────────────┘    └──────────────┬───────────────────────────────┘   │    │
│  │                                     │                                    │    │
│  │              ┌──────────────────────┼──────────────────────┐            │    │
│  │              ▼                      ▼                      ▼            │    │
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────┐   │    │
│  │  │   Web Frontend   │  │   FastAPI Backend   │  │  Stirling-PDF /    │   │    │
│  │  │  (HTMX + Tailwind)│  │  (REST + WebSocket) │  │   Tesseract OCR    │   │    │
│  │  └────────┬─────────┘  └─────────┬──────────┘  └────────────────────┘   │    │
│  │           │                      │                                        │    │
│  │           │         ┌────────────┼────────────┐                          │    │
│  │           │         ▼            ▼            ▼                          │    │
│  │           │  ┌──────────┐ ┌──────────┐ ┌──────────┐                     │    │
│  │           │  │PostgreSQL│ │Elastic-  │ │  Redis   │                     │    │
│  │           │  │(Metadata │ │  search  │ │(Queue &  │                     │    │
│  │           │  │+ Audit)  │ │(Full-Text│ │ Cache)   │                     │    │
│  │           │  └──────────┘ └──────────┘ └──────────┘                     │    │
│  │           │                                                              │    │
│  │           ▼                                                              │    │
│  │  ┌──────────────────┐                                                    │    │
│  │  │ Ingestion Worker │◀─── Forensic Drop Zone (Upload Portal)             │    │
│  │  │  (Celery)        │     .eml .mbox .pst .pdf .docx .xlsx               │    │
│  │  └────────┬─────────┘                                                    │    │
│  │           │                                                              │    │
│  │           ▼                                                              │    │
│  │  ┌──────────────────┐                                                    │    │
│  │  │   Azure Blob     │◀─── Artifact Storage (SAS Token Access)             │    │
│  │  │   Storage        │                                                    │    │
│  │  └──────────────────┘                                                    │    │
│  │                                                                          │    │
│  │  ┌──────────────────────────────────────────────────────────────┐        │    │
│  │  │              Microsoft Entra ID (OIDC)                       │        │    │
│  │  │         Authentication & Role-Based Access                   │        │    │
│  │  └──────────────────────────────────────────────────────────────┘        │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **Upload**: User uploads forensic exports to the secure Drop Zone
2. **Queue**: Files are staged and queued in Redis for processing
3. **Ingest**: Worker extracts metadata, attachments, and text content
4. **OCR**: Non-selectable PDFs/images are sent through Tesseract
5. **Index**: Processed documents are indexed in Elasticsearch
6. **Store**: Original artifacts persist in Azure Blob Storage
7. **Audit**: Every action is immutably logged in PostgreSQL

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your Azure credentials

# 2. Start the stack
docker compose up -d

# 3. Initialize the database
docker compose exec web python -m src.models.init_db

# 4. Access the application
# Web UI:      https://localhost
# API Docs:    https://localhost/api/docs
# Drop Zone:   https://localhost/upload
```

## Project Structure

```
gatekeep/
├── docker-compose.yml          # Main orchestration
├── docker-compose.prod.yml     # Production overrides
├── .env.example                # Environment template
├── db/init/                    # Database initialization scripts
│   └── 001_schema.sql          # Core schema (audit_logs, document_metadata)
├── src/
│   ├── api/                    # FastAPI routes
│   │   ├── main.py             # Application entry point
│   │   ├── documents.py        # Document CRUD endpoints
│   │   ├── search.py           # Elasticsearch query endpoints
│   │   ├── upload.py           # Drop zone upload endpoints
│   │   └── audit.py            # Audit log query endpoints
│   ├── auth/                   # Authentication
│   │   ├── entra.py            # Microsoft Entra ID OIDC integration
│   │   └── middleware.py       # JWT validation & RBAC
│   ├── ingestion/              # Document processing pipeline
│   │   ├── pipeline.py         # Orchestration logic
│   │   ├── email_parser.py     # EML/MBOX/PST extraction
│   │   ├── office_parser.py    # DOCX/XLSX/PPTX extraction
│   │   └── pdf_handler.py      # PDF text/image extraction
│   ├── storage/                # Storage abstraction
│   │   └── azure_blob.py       # Azure Blob Storage client
│   ├── models/                 # SQLAlchemy models
│   │   ├── database.py         # Database connection
│   │   ├── audit_log.py        # Immutable audit log model
│   │   └── document.py         # Document metadata model
│   ├── middleware/
│   │   └── audit.py            # Auto-audit logging middleware
│   └── frontend/               # Web interface
│       ├── static/             # CSS, JS assets
│       └── templates/          # Jinja2 HTML templates
├── workers/
│   ├── ingestion/
│   │   ├── worker.py           # Celery ingestion worker
│   │   └── tasks.py            # Celery task definitions
│   └── ocr/
│       └── processor.py        # Tesseract OCR processor
├── k8s/                        # Kubernetes manifests for AKS
│   ├── web/
│   ├── worker/
│   ├── postgres/
│   ├── elasticsearch/
│   └── ingress/
├── scripts/
│   ├── seed_elasticsearch.py   # Elasticsearch index setup
│   └── backup.sh               # Backup automation
└── docs/
    └── DEPLOYMENT.md           # Azure deployment guide
```

## Core Components

### Ingestion Pipeline
- **Email Formats**: `.eml`, `.mbox`, `.pst` (via `extract-msg`, `mailbox`, `libpff`)
- **Office Formats**: `.docx`, `.xlsx`, `.pptx` (via `python-pptx`, `openpyxl`, `python-docx`)
- **PDF**: Text extraction via `pypdf`, OCR via Tesseract/Stirling-PDF
- **Images**: TIFF, PNG, JPG OCR processing

### Search Capabilities
- Boolean operators: `AND`, `OR`, `NOT`, parentheses grouping
- Date range filtering
- Field-specific search: `author:`, `date:`, `type:`, `subject:`
- Fuzzy matching with configurable edit distance
- Result highlighting

### Audit Trail
- Write-once append-only table
- Cryptographic hash chaining for tamper detection
- Captures: uploads, views, searches, exports, deletions
- User ID, timestamp, IP address, action type, resource ID

## Azure Integration

### Authentication
- Microsoft Entra ID via OIDC
- Role-based access control (Admin, Paralegal, Attorney, Auditor)
- Group-based authorization

### Storage
- Azure Blob Storage for document artifacts
- SAS token-based secure access
- Azure Files (SMB/NFS) for persistent volumes

### Networking
- VNET integration for private communication
- Private Endpoints for Blob Storage and PostgreSQL
- SSL/TLS termination at Application Gateway or Traefik

## Deployment Options

| Option | Use Case | Management Overhead |
|--------|----------|---------------------|
| Docker Compose | Dev / Small firm | Low |
| Azure Container Apps | Medium firm | Medium |
| AKS (Kubernetes) | Large firm / Multi-tenant | High |

## Security Considerations

- All data encrypted at rest (Azure Storage Service Encryption)
- TLS 1.2+ for all in-transit communication
- RBAC with least-privilege defaults
- Immutable audit logs with hash chaining
- Regular automated backups
- Private networking (no public endpoints required)

## License

Proprietary - For authorized use only.
