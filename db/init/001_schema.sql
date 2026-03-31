-- GateKeep Database Schema
-- PostgreSQL 16+
-- Immutable audit trail with hash chaining for tamper detection

-- ──────────────────────────────────────────────
-- Extensions
-- ──────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ──────────────────────────────────────────────
-- Users (synced from Entra ID)
-- ──────────────────────────────────────────────
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entra_oid       VARCHAR(255) UNIQUE NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    display_name    VARCHAR(255) NOT NULL,
    role            VARCHAR(32) NOT NULL DEFAULT 'paralegal'
                    CHECK (role IN ('admin', 'attorney', 'paralegal', 'auditor', 'viewer')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    last_login      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_entra_oid ON users(entra_oid);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);

-- ──────────────────────────────────────────────
-- Matters / Cases
-- ──────────────────────────────────────────────
CREATE TABLE matters (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    matter_number   VARCHAR(64) UNIQUE NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    client_name     VARCHAR(255),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_matters_number ON matters(matter_number);

-- Matter access control (which users can access which matters)
CREATE TABLE matter_access (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    matter_id       UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    access_level    VARCHAR(32) NOT NULL DEFAULT 'viewer'
                    CHECK (access_level IN ('owner', 'editor', 'viewer')),
    granted_by      UUID REFERENCES users(id),
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(matter_id, user_id)
);

CREATE INDEX idx_matter_access_matter ON matter_access(matter_id);
CREATE INDEX idx_matter_access_user ON matter_access(user_id);

-- ──────────────────────────────────────────────
-- Document Metadata
-- ──────────────────────────────────────────────
CREATE TABLE document_metadata (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    matter_id               UUID REFERENCES matters(id) ON DELETE SET NULL,

    -- File identification
    original_filename       VARCHAR(512) NOT NULL,
    file_extension          VARCHAR(16) NOT NULL,
    mime_type               VARCHAR(128),
    file_size_bytes         BIGINT NOT NULL,
    sha256_hash             VARCHAR(64) NOT NULL,

    -- Storage location
    azure_blob_url          TEXT NOT NULL,
    azure_blob_container    VARCHAR(255) NOT NULL,
    azure_blob_name         TEXT NOT NULL,

    -- Extracted metadata (varies by document type)
    author                  VARCHAR(255),
    title                   TEXT,
    subject                 TEXT,
    created_date            TIMESTAMPTZ,
    modified_date           TIMESTAMPTZ,

    -- Email-specific fields
    sender_email            VARCHAR(255),
    sender_name             VARCHAR(255),
    recipient_emails        TEXT[],
    cc_emails               TEXT[],
    sent_date               TIMESTAMPTZ,
    received_date           TIMESTAMPTZ,
    email_subject           TEXT,
    message_id              VARCHAR(512),
    in_reply_to             VARCHAR(512),
    has_attachments         BOOLEAN DEFAULT false,
    attachment_count        INTEGER DEFAULT 0,
    parent_document_id      UUID REFERENCES document_metadata(id),

    -- Processing status
    ocr_status              VARCHAR(32) DEFAULT 'pending'
                            CHECK (ocr_status IN ('pending', 'processing', 'completed', 'failed', 'not_needed')),
    ocr_text_length         INTEGER DEFAULT 0,
    extraction_status       VARCHAR(32) DEFAULT 'pending'
                            CHECK (extraction_status IN ('pending', 'processing', 'completed', 'failed')),
    language                VARCHAR(16) DEFAULT 'en',

    -- Audit
    uploaded_by             UUID NOT NULL REFERENCES users(id),
    uploaded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at            TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Full-text search support columns
ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(original_filename, '')), 'A')
        || setweight(to_tsvector('english', coalesce(author, '')), 'B')
        || setweight(to_tsvector('english', coalesce(title, '')), 'B')
        || setweight(to_tsvector('english', coalesce(subject, '')), 'B')
        || setweight(to_tsvector('english', coalesce(email_subject, '')), 'B')
        || setweight(to_tsvector('english', coalesce(sender_name, '')), 'C')
        || setweight(to_tsvector('english', coalesce(sender_email, '')), 'C')
    ) STORED;

CREATE INDEX idx_doc_search_vector ON document_metadata USING GIN(search_vector);
CREATE INDEX idx_doc_matter ON document_metadata(matter_id);
CREATE INDEX idx_doc_sha256 ON document_metadata(sha256_hash);
CREATE INDEX idx_doc_uploaded_by ON document_metadata(uploaded_by);
CREATE INDEX idx_doc_sent_date ON document_metadata(sent_date);
CREATE INDEX idx_doc_extension ON document_metadata(file_extension);
CREATE INDEX idx_doc_sender_email ON document_metadata(sender_email);
CREATE INDEX idx_doc_author ON document_metadata(author);
CREATE INDEX idx_doc_extraction_status ON document_metadata(extraction_status);
CREATE INDEX idx_doc_parent ON document_metadata(parent_document_id);

-- ──────────────────────────────────────────────
-- Immutable Audit Log
-- Uses hash chaining to detect tampering
-- ──────────────────────────────────────────────
CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL DEFAULT uuid_generate_v4(),

    -- Chain integrity (each row hashes the previous row)
    prev_hash       VARCHAR(64),
    row_hash        VARCHAR(64) NOT NULL,

    -- Event details
    action          VARCHAR(64) NOT NULL
                    CHECK (action IN (
                        'upload', 'view', 'download', 'search', 'export',
                        'delete', 'ocr_process', 'metadata_update',
                        'login', 'logout', 'permission_change',
                        'matter_create', 'matter_update', 'matter_access_grant'
                    )),
    resource_type   VARCHAR(64) NOT NULL,
    resource_id     UUID,
    matter_id       UUID REFERENCES matters(id),

    -- Actor
    user_id         UUID REFERENCES users(id),
    user_email      VARCHAR(255),

    -- Context
    ip_address      INET,
    user_agent      TEXT,
    details         JSONB,

    -- Timestamp (immutable once set)
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prevent any updates or deletes on audit_logs
CREATE OR REPLACE FUNCTION audit_logs_prevent_modify()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log entries are immutable and cannot be modified or deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_immutable
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_prevent_modify();

-- Hash chain trigger
CREATE OR REPLACE FUNCTION audit_logs_compute_hash()
RETURNS TRIGGER AS $$
DECLARE
    content TEXT;
BEGIN
    IF NEW.prev_hash IS NULL THEN
        NEW.prev_hash := lpad('0', 64, '0');
    END IF;

    content := NEW.prev_hash || NEW.action || NEW.resource_type ||
               coalesce(NEW.resource_id::text, '') || coalesce(NEW.user_id::text, '') ||
               coalesce(NEW.user_email, '') || coalesce(NEW.ip_address::text, '') ||
               NEW.timestamp::text || coalesce(NEW.details::text, '');

    NEW.row_hash := encode(sha256(content::bytea), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_hash_before_insert
    BEFORE INSERT ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_compute_hash();

CREATE INDEX idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX idx_audit_user ON audit_logs(user_id);
CREATE INDEX idx_audit_action ON audit_logs(action);
CREATE INDEX idx_audit_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_matter ON audit_logs(matter_id);

-- ──────────────────────────────────────────────
-- Search History (for analytics, not immutable)
-- ──────────────────────────────────────────────
CREATE TABLE search_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id),
    query_text      TEXT NOT NULL,
    filters         JSONB,
    result_count    INTEGER,
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_search_history_user ON search_history(user_id);
CREATE INDEX idx_search_history_executed ON search_history(executed_at);

-- ──────────────────────────────────────────────
-- Processing Queue Tracking
-- ──────────────────────────────────────────────
CREATE TABLE processing_queue (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID REFERENCES document_metadata(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    task_type       VARCHAR(32) NOT NULL
                    CHECK (task_type IN ('extract', 'ocr', 'index', 'full')),
    status          VARCHAR(32) NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'retry')),
    retry_count     INTEGER DEFAULT 0,
    max_retries     INTEGER DEFAULT 3,
    error_message   TEXT,
    celery_task_id  VARCHAR(255),
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_processing_queue_status ON processing_queue(status);
CREATE INDEX idx_processing_queue_document ON processing_queue(document_id);
CREATE INDEX idx_processing_queue_task_type ON processing_queue(task_type);

-- ──────────────────────────────────────────────
-- Initial admin user seed (optional, override via Entra ID)
-- ──────────────────────────────────────────────
-- INSERT INTO users (entra_oid, email, display_name, role)
-- VALUES ('local-admin', 'admin@yourfirm.com', 'System Administrator', 'admin')
-- ON CONFLICT (entra_oid) DO NOTHING;
