# Automated Multi-Platform Posting

A FastAPI service that publishes a short message and one HTTP(S) CTA link to five configured destinations. Platform selection is server-side. Requests run concurrently and provider failures appear independently in the response.

## Setup

Use Python 3.11+. Create and activate a virtual environment, then run:

```sh
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 4000 --env-file .env
```

Fill `.env` with credentials for destinations you control. The API has no client authentication and is designed for local use. Use disposable test destinations for live smoke tests.

| Platform | Configuration | API reference |
|---|---|---|
| Bluesky | A bsky.social account handle and an app password | [AT Protocol createSession](https://docs.bsky.app/docs/api/com-atproto-server-create-session), [createRecord](https://docs.bsky.app/docs/api/com-atproto-repo-create-record) |
| Mastodon | HTTPS instance origin and user access token with `write:statuses` | [Create status](https://docs.joinmastodon.org/methods/statuses/#create) |
| Slack | Bot token with `chat:write` and a channel ID. Invite the bot to the channel. | [Create message](https://docs.slack.dev/reference/methods/chat.postMessage/), [Permalink](https://api.slack.com/methods/chat.getPermalink) |
| Discord | Channel webhook URL and its server ID for the permalink | [Execute webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook) |
| Telegram | Bot token and destination chat ID. A channel requires posting rights. | [Send message](https://core.telegram.org/bots/api#sendmessage) |

## API

Swagger UI is at http://localhost:4000/docs.

```sh
curl -sS http://localhost:4000/posting -H 'Content-Type: application/json' \
  -d '{"message":"See the project demo https://example.com"}'
```

The message must contain exactly one explicit HTTP(S) URL and be at most 200 characters. The service validates URL syntax and never fetches the CTA. Input errors return HTTP 422. A valid batch returns HTTP 200, including when every provider fails. The top-level `success` is true if at least one provider accepted the post.

Illustrative response excerpt:

```json
{
  "success": true,
  "results": [
    {
      "platform": "Bluesky",
      "status": "success",
      "posted_url": "https://bsky.app/profile/did:plc:demo/post/123",
      "public_access": true,
      "date": "2026-11-15",
      "time": "10:30:00",
      "error": null
    },
    {
      "platform": "Mastodon",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "Provider returned HTTP 429"
    }
  ]
}
```

Every actual response contains five results. Dates and times are UTC acceptance timestamps recorded by the service. Slack and Discord links require membership. Telegram reports public access for public channels and supergroups. Visibility is based on the provider response and destination type. A successful post can have a missing permalink with an explanation in `error`. The service avoids automatically retrying publishing calls because a timeout can occur after a provider has already accepted the post.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests exercise the FastAPI endpoint and all five adapters with mocked provider HTTP responses. They cover a full successful batch, a batch with a rate-limited platform, invalid inputs and missing credentials. See [test-results.txt](test-results.txt). Live posting requires account credentials and was not exercised.

## Optional LLM integration

Set `OPENAI_REVIEW_MODEL` to a model available to your API account and set
`OPENAI_API_KEY` in `.env`. The configured service then sends the submitted
message to the [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
with response storage disabled and returns copy feedback in `llm_review`.
Leave the model unset to disable review and avoid the provider call.

The review runs concurrently with the five publishers. It provides feedback only.
Publishers send the original copy, and a review failure does not affect posting
results. A model review is a private API response without a public post URL.
The tests mock both successful and failed model responses.

## Browser dashboard

Try the complete form and results flow with no credentials or outbound requests:

```sh
POSTING_DEMO_MODE=true uvicorn app.main:app --host 127.0.0.1 --port 4000
```

Open http://localhost:4000/, click **Use sample**, then **Run demo**. Demo mode
validates the request and simulates the five responses without external calls.
For real publishing, restart using the live setup above with demo mode unset.
The dashboard calls the same `POST /posting` endpoint in both modes.

## Queued publishing

Install dependencies, run a local Redis server, and start these in separate shells:

```sh
POSTING_DEMO_MODE=true celery -A app.queue:worker worker --loglevel=info
```

```sh
POSTING_QUEUE_ENABLED=true POSTING_DEMO_MODE=true uvicorn app.main:app --host 127.0.0.1 --port 4000
```

The dashboard lets you select destinations. It sends one job to `POST /jobs`,
then polls `GET /jobs/{job_id}`. Each destination has an independent Celery task
and status. Redis stores jobs and results for 24 hours. If a broker publish fails
ambiguously, check the destination before resubmitting. No automatic posting
retry is performed because the provider may have accepted the original request.

For live posting, start the worker with provider environment variables and demo
mode unset. Keep API and worker demo settings identical. Use only on localhost
or behind authentication. Existing direct `POST /posting` remains available.

Queued text can omit the CTA link. In queued demo mode, choose a photo, video,
or audio file with or without a caption. The browser previews the file locally
and sends only its media type to simulate the job. Live media upload and
provider-specific attachment support are pending. The direct assignment
endpoint still enforces its CTA requirement.
