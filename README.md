# GateKeep - Legal Discovery & Document Management System (The Walled Garden)

[![Build and Push Docker Images](https://github.com/anomalyco/gatekeep/actions/workflows/docker-build.yml/badge.svg)](https://github.com/anomalyco/gatekeep/actions/workflows/docker-build.yml)

A self-hosted, containerized legal discovery platform designed for Azure environments. Provides secure forensic document ingestion, OCR processing, full-text search, immutable audit trails, and **strict matter-level isolation**.

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

## Matter Isolation (The Walled Garden)

Every attorney works within isolated **matters** (cases). Data never bleeds between matters.

```
jclark@lawfirm logs in
       │
       ├── Selects Matter "2024-CV-00123 - Smith v. Jones"
       │       ├── Search: only documents in THIS matter
       │       ├── Upload: files tagged to THIS matter
       │       ├── Documents: only THIS matter's files
       │       ├── Members: only users granted access to THIS matter
       │       └── Audit: all actions scoped to THIS matter
       │
       └── Switches to Matter "2024-IP-00456 - TechCorp Patent"
               ├── Completely separate document set
               ├── Different members, different permissions
               └── Zero data overlap with previous matter
```

### Enforcement Points

| Layer | Mechanism |
|-------|-----------|
| **API** | `require_matter` dependency on every data endpoint — rejects requests without valid `X-Matter-ID` |
| **Database** | All queries include `WHERE matter_id = :id` — no cross-matter SQL possible |
| **Search** | Elasticsearch filter `{"term": {"matter_id": "..."}}` is mandatory — no global search across matters |
| **Storage** | Blob paths are `{matter_id}/{task_id}/{filename}` — artifacts physically separated |
| **Access** | `matter_access` table enforces per-matter RBAC (owner/editor/viewer) |
| **Frontend** | No data loads until a matter is selected; all requests include the matter context |

### Matter Lifecycle (Self-Serve, No Admin Needed)

1. **Create** — Any authenticated user creates a matter, becomes `owner` automatically
2. **Grant Access** — Owner adds colleagues by email with `viewer` or `editor` roles
3. **Upload** — Editors and owners upload documents to the matter's drop zone
4. **Archive** — Owner archives a matter when the case closes (becomes read-only)
5. **Export** — Download a portable `.zip` with all documents + metadata + manifest
6. **Import** — Upload an exported `.zip` to create a new matter with all content intact

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/anomalyco/gatekeep.git
cd gatekeep
cp .env.example .env
# Edit .env with your Azure credentials

# 2. Start the stack
docker compose up -d

# 3. Access the application
# Web UI:      http://localhost:8000
# API Docs:    http://localhost:8000/api/docs
# Flower:      http://localhost:5555  (docker compose --profile monitoring up -d)
```

## Building Docker Images

### Local Build

```bash
docker compose build
```

### Via GitHub Actions

Push to `main` or tag with `v*` and the workflow builds + pushes to GHCR:

```
ghcr.io/<owner>/gatekeep/gatekeep-web:latest
ghcr.io/<owner>/gatekeep/gatekeep-worker:latest
```

Tags are generated from:
- Branch name (`main`, `feature/xyz`)
- Semver tags (`v1.0.0`, `v1.0`)
- Git SHA

### Use Pre-built Images

```bash
GATEKEEP_REGISTRY=ghcr.io/your-org/gatekeep IMAGE_TAG=latest docker compose up -d
```

## Project Structure

```
gatekeep/
├── .github/workflows/
│   └── docker-build.yml          # CI: build & push to GHCR
├── docker-compose.yml            # Main orchestration (builds or pulls images)
├── docker-compose.prod.yml       # Production overrides
├── .env.example                  # Environment template
├── db/init/
│   └── 001_schema.sql            # Core schema (matters, audit_logs, document_metadata)
├── src/
│   ├── api/
│   │   ├── main.py               # Application entry point
│   │   ├── matters.py            # Matter CRUD, access control, archive
│   │   ├── matter_import_export.py # Portable matter export/import
│   │   ├── documents.py          # Document CRUD (matter-scoped)
│   │   ├── search.py             # Elasticsearch search (matter-scoped)
│   │   ├── upload.py             # Drop zone upload (matter-scoped)
│   │   ├── audit.py              # Audit log queries + chain verification
│   │   └── auth.py               # Entra ID OIDC authentication
│   ├── middleware/
│   │   ├── audit.py              # Auto-audit logging on every request
│   │   └── matter_scope.py       # Matter isolation enforcement
│   ├── ingestion/
│   │   ├── pipeline.py           # File type routing
│   │   ├── email_parser.py       # EML/MBOX/PST extraction
│   │   ├── office_parser.py      # DOCX/XLSX/PPTX extraction
│   │   ├── pdf_handler.py        # PDF text + OCR detection
│   │   └── models.py             # ExtractedDocument dataclass
│   ├── storage/
│   │   └── azure_blob.py         # Azure Blob Storage client
│   ├── models/
│   │   ├── database.py           # Async SQLAlchemy setup
│   │   ├── audit_log.py          # Immutable audit log model
│   │   └── document.py           # Document metadata model
│   ├── config.py                 # Pydantic settings
│   └── frontend/templates/
│       └── index.html            # Matter-aware SPA (Tailwind + vanilla JS)
├── workers/
│   ├── ingestion/
│   │   ├── worker.py             # Celery app config
│   │   └── tasks.py              # Batch processing, OCR, indexing tasks
│   └── ocr/
│       └── processor.py          # Tesseract + Stirling-PDF OCR
├── k8s/                          # Kubernetes manifests for AKS
├── scripts/
│   ├── backup.sh                 # PostgreSQL + Elasticsearch backup
│   └── seed_elasticsearch.py     # ES index initialization
└── docs/
    └── DEPLOYMENT.md             # Azure deployment guide
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
- **Always scoped to the active matter** — no cross-matter results

### Audit Trail
- Write-once append-only table (PostgreSQL trigger prevents UPDATE/DELETE)
- Cryptographic hash chaining for tamper detection
- Captures: uploads, views, searches, exports, deletions, matter operations
- User ID, timestamp, IP address, action type, resource ID

### Matter Access Control

| Role | Create Matter | Upload | View | Search | Manage Members | Archive |
|------|:-------------:|:------:|:----:|:------:|:--------------:|:-------:|
| Owner | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Editor | — | ✓ | ✓ | ✓ | — | — |
| Viewer | — | — | ✓ | ✓ | — | — |

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

- **Matter isolation** — strict boundaries enforced at API, database, search, and storage layers
- All data encrypted at rest (Azure Storage Service Encryption)
- TLS 1.2+ for all in-transit communication
- RBAC with least-privilege defaults
- Immutable audit logs with hash chaining
- Regular automated backups
- Private networking (no public endpoints required)

## License

Proprietary - For authorized use only.
