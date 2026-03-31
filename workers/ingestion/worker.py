import os
from celery import Celery
from src.config import settings

celery_app = Celery(
    "gatekeep",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="ingestion",
    task_queues={
        "ingestion": {"exchange": "ingestion", "routing_key": "ingestion"},
        "ocr": {"exchange": "ocr", "routing_key": "ocr"},
        "index": {"exchange": "index", "routing_key": "index"},
    },
    beat_schedule={
        "cleanup-stale-tasks": {
            "task": "workers.ingestion.tasks.cleanup_stale_tasks",
            "schedule": 3600.0,
        },
    },
)
