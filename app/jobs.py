from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.main import TARGETS, demo_mode
from app.queue import publish_destination, worker

router = APIRouter()
PLATFORMS = tuple(name for name, _ in TARGETS)


class QueueRequest(BaseModel):
    message: str = Field(default="", max_length=200)
    media_type: Literal["image", "video", "audio"] | None = None
    platforms: list[str] = Field(min_length=1, max_length=len(PLATFORMS))


class DestinationJob(BaseModel):
    platform: str
    task_id: str


class Job(BaseModel):
    job_id: UUID
    destinations: list[DestinationJob]


def job_key(job_id: UUID) -> str:
    return f"posting:job:{job_id}"


@router.post("/jobs", status_code=202)
def enqueue(body: QueueRequest):
    if not body.message.strip() and body.media_type is None:
        raise HTTPException(422, "Enter a message or choose a media file")
    if body.media_type and not demo_mode():
        raise HTTPException(422, "Media publishing requires a media-capable connector")
    if len(set(body.platforms)) != len(body.platforms) or not set(body.platforms) <= set(PLATFORMS):
        raise HTTPException(422, "Select distinct supported platforms")
    job = Job(job_id=uuid4(), destinations=[
        DestinationJob(platform=name, task_id=str(uuid4())) for name in body.platforms
    ])
    try:
        worker.backend.client.setex(job_key(job.job_id), 86400, job.model_dump_json())
    except Exception:
        raise HTTPException(503, "Job storage unavailable") from None

    for destination in job.destinations:
        try:
            publish_destination.apply_async(
                args=(destination.platform, body.message), task_id=destination.task_id,
            )
        except Exception:
            # The broker might have received the task before disconnecting.
            try:
                worker.backend.client.setex(
                    f"posting:uncertain:{destination.task_id}", 86400, "1",
                )
            except Exception:
                continue
    return {"job_id": job.job_id, "status_url": f"/jobs/{job.job_id}"}


@router.get("/jobs/{job_id}")
def status(job_id: UUID):
    try:
        raw = worker.backend.client.get(job_key(job_id))
        if raw is None:
            raise HTTPException(404, "Job expired or not found")
        job = Job.model_validate_json(raw)
        results = []
        for destination in job.destinations:
            meta = worker.backend.get_task_meta(destination.task_id)
            state = meta["status"]
            result = meta["result"] if state == "SUCCESS" else None
            uncertain = state == "PENDING" and worker.backend.client.exists(
                f"posting:uncertain:{destination.task_id}"
            )
            status = {
                "PENDING": "dispatch_unknown" if uncertain else "queued",
                "STARTED": "publishing",
                "FAILURE": "failed",
                "REVOKED": "failed",
            }.get(state, "publishing")
            if result is not None:
                status = result["status"]
            results.append({"platform": destination.platform, "status": status, "result": result})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Job status unavailable") from None
    return {"job_id": job_id, "results": results}
