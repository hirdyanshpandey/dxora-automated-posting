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
