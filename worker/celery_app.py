from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from worker.config import settings


app = Celery(
    "spectrum",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "worker.tasks.embed",
        "worker.tasks.cluster",
    ],
)


app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=120,  # 2 min — levanta SoftTimeLimitExceeded
    task_time_limit=180,  # 3 min — mata o worker se travar
    beat_schedule={
        "coverage-digest-every-six-hours": {
            "task": "worker.tasks.cluster.send_coverage_digest",
            "schedule": crontab(minute=0, hour="*/6"),
        },
    },
)
