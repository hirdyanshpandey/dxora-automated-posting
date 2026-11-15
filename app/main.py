import asyncio
import re
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, field_validator

from app import platforms

app = FastAPI(title="Automated Multi-Platform Posting")
LINK = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"']+", re.I)
url_adapter = TypeAdapter(HttpUrl)


class PostingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(max_length=200)

    @field_validator("message")
    @classmethod
    def validate_link(cls, message: str) -> str:
        links = LINK.findall(message)
        if len(links) != 1:
            raise ValueError("Include exactly one HTTP or HTTPS CTA link")
        try:
            url = url_adapter.validate_python(links[0].rstrip(".,!?;:)]}"))
        except ValidationError:
            raise ValueError("CTA link must be a valid HTTP or HTTPS URL") from None
        if url.username is not None or url.password is not None:
            raise ValueError("CTA link must not contain credentials")
        return message.strip()


class Result(BaseModel):
    platform: str
    status: Literal["success", "failed"]
    posted_url: str | None = None
    public_access: bool = False
    date: str | None = None
    time: str | None = None
    error: str | None = None


class PostingResponse(BaseModel):
    success: bool
    results: list[Result]


TARGETS = (
    ("Bluesky", platforms.bluesky),
    ("Mastodon", platforms.mastodon),
    ("Slack", platforms.slack),
    ("Discord", platforms.discord),
    ("Telegram", platforms.telegram),
)


def make_client():
    return httpx.AsyncClient(timeout=10, trust_env=False)


async def publish_one(client, name, publish, message, link):
    try:
        async with asyncio.timeout(20):
            url, public, warning = await publish(client, message, link)
    except platforms.ProviderError as exc:
        return Result(platform=name, status="failed", error=str(exc))
    except httpx.HTTPStatusError as exc:
        return Result(platform=name, status="failed", error=f"Provider returned HTTP {exc.response.status_code}")
    except (TimeoutError, httpx.TimeoutException):
        return Result(platform=name, status="failed", error="Provider timed out. Check the destination before retrying.")
    except httpx.RequestError:
        return Result(platform=name, status="failed", error="Provider could not be reached")
    except (KeyError, TypeError, ValueError):
        return Result(platform=name, status="failed", error="Unexpected provider response. Check the destination before retrying.")

    now = datetime.now(timezone.utc)
    return Result(
        platform=name, status="success", posted_url=url, public_access=public,
        date=now.date().isoformat(), time=now.strftime("%H:%M:%S"), error=warning,
    )


@app.post("/posting", response_model=PostingResponse)
async def posting(body: PostingRequest):
    link = LINK.search(body.message).group().rstrip(".,!?;:)]}")
    async with make_client() as client:
        results = await asyncio.gather(*(
            publish_one(client, name, publish, body.message, link)
            for name, publish in TARGETS
        ))
    return PostingResponse(success=any(result.status == "success" for result in results), results=results)
