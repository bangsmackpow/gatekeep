#!/usr/bin/env python3
"""
GateKeep - Elasticsearch Index Initialization Script
Run this to set up Elasticsearch indices before first use.

Usage:
    python scripts/seed_elasticsearch.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import AsyncElasticsearch
from src.config import settings


async def init_elasticsearch():
    es = AsyncElasticsearch(
        settings.ELASTICSEARCH_URL,
        basic_auth=(settings.ELASTICSEARCH_USER, settings.ELASTICSEARCH_PASSWORD),
        verify_certs=False,
        request_timeout=30,
    )

    try:
        health = await es.cluster.health()
        print(f"Elasticsearch cluster status: {health['status']}")
    except Exception as e:
        print(f"Cannot connect to Elasticsearch: {e}")
        sys.exit(1)

    indices = {
        "documents": {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "email_analyzer": {
                            "type": "custom",
                            "tokenizer": "uax_url_email",
                            "filter": ["lowercase", "unique"],
                        }
                    }
                }
            },
            "mappings": {
                "properties": {
                    "document_id": {"type": "keyword"},
                    "original_filename": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {
                            "keyword": {"type": "keyword", "ignore_above": 512},
                        }
                    },
                    "author": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "email_subject": {"type": "text", "analyzer": "standard"},
                    "sender_email": {
                        "type": "text",
                        "analyzer": "email_analyzer",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "sender_name": {"type": "text", "analyzer": "standard"},
                    "recipient_emails": {"type": "keyword"},
                    "subject": {"type": "text", "analyzer": "standard"},
                    "title": {"type": "text", "analyzer": "standard"},
                    "extracted_text": {
                        "type": "text",
                        "analyzer": "standard",
                        "term_vector": "with_positions_offsets",
                    },
                    "ocr_text": {
                        "type": "text",
                        "analyzer": "standard",
                        "term_vector": "with_positions_offsets",
                    },
                    "file_extension": {"type": "keyword"},
                    "mime_type": {"type": "keyword"},
                    "matter_id": {"type": "keyword"},
                    "sha256_hash": {"type": "keyword"},
                    "sent_date": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
                    "received_date": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
                    "created_date": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
                    "uploaded_at": {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
                    "language": {"type": "keyword"},
                }
            }
        }
    }

    for index_name, config in indices.items():
        exists = await es.indices.exists(index=index_name)
        if exists:
            print(f"Index '{index_name}' already exists, skipping.")
            continue

        await es.indices.create(index=index_name, **config)
        print(f"Created index: {index_name}")

    await es.close()
    print("Elasticsearch initialization complete.")


if __name__ == "__main__":
    asyncio.run(init_elasticsearch())
