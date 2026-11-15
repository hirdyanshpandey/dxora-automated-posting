import asyncio
import os

from celery import Celery

worker = Celery(
    "posting",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)
worker.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
    task_publish_retry=False,
)


@worker.task(name="posting.publish_destination")
def publish_destination(name: str, message: str):
    from app.main import run_destination

    return asyncio.run(run_destination(name, message)).model_dump(mode="json")
