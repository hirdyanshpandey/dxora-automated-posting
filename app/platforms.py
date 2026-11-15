import asyncio
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit


class ProviderError(Exception):
    pass


def required(*names):
    values = [os.getenv(name) for name in names]
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise ProviderError("Missing configuration: " + ", ".join(missing))
    return values


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def request(client, method, url, **kwargs):
    response = await client.request(method, url, **kwargs)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ProviderError("Unexpected provider response")
    if data.get("ok") is False:
        raise ProviderError("Provider rejected the request. Check credentials and destination permissions.")
    return data


async def bluesky(client, message, link):
    handle, password = required("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD")
    origin = "https://bsky.social/xrpc"
    session = await request(client, "POST", f"{origin}/com.atproto.server.createSession",
                            json={"identifier": handle, "password": password})
    start = len(message[:message.index(link)].encode("utf-8"))
    record = {
        "$type": "app.bsky.feed.post",
        "text": message,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "facets": [{
            "index": {"byteStart": start, "byteEnd": start + len(link.encode("utf-8"))},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}],
        }],
    }
    post = await request(client, "POST", f"{origin}/com.atproto.repo.createRecord",
                         headers=bearer(session["accessJwt"]),
                         json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record})
    key = post["uri"].rsplit("/", 1)[1]
    return f"https://bsky.app/profile/{session['did']}/post/{key}", True, None


async def mastodon(client, message, link):
    origin, token = required("MASTODON_ORIGIN", "MASTODON_TOKEN")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProviderError("MASTODON_ORIGIN must be an HTTPS instance origin")
    post = await request(client, "POST", f"{origin.rstrip('/')}/api/v1/statuses",
                         headers=bearer(token), json={"status": message, "visibility": "public"})
    url = post.get("url")
    return url, post.get("visibility") == "public", None if url else "Provider omitted the post URL"


async def slack(client, message, link):
    token, channel = required("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")
    headers = bearer(token)
    post = await request(client, "POST", "https://slack.com/api/chat.postMessage", headers=headers,
                         json={"channel": channel, "text": message, "unfurl_links": False,
                               "unfurl_media": False, "mrkdwn": False})
    try:
        async with asyncio.timeout(5):
            permalink = await request(client, "GET", "https://slack.com/api/chat.getPermalink",
                                      headers=headers, params={"channel": post["channel"], "message_ts": post["ts"]})
        return permalink["permalink"], False, None
    except (TimeoutError, httpx.HTTPError, ProviderError, KeyError, ValueError):
        return None, False, "Message posted, but its permalink could not be retrieved"


async def discord(client, message, link):
    (webhook,) = required("DISCORD_WEBHOOK_URL")
    parsed = urlsplit(webhook)
    if parsed.scheme != "https" or parsed.netloc != "discord.com" or not parsed.path.startswith("/api/webhooks/") or parsed.query or parsed.fragment:
        raise ProviderError("DISCORD_WEBHOOK_URL must be a Discord HTTPS webhook URL")
    post = await request(client, "POST", webhook, params={"wait": "true"},
                         json={"content": message, "allowed_mentions": {"parse": []}})
    guild = post.get("guild_id") or os.getenv("DISCORD_GUILD_ID")
    if not guild:
        return None, False, "Message posted. Set DISCORD_GUILD_ID to construct its permalink."
    return f"https://discord.com/channels/{guild}/{post['channel_id']}/{post['id']}", False, None


async def telegram(client, message, link):
    token, chat = required("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    post = await request(client, "POST", f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": chat, "text": message, "link_preview_options": {"is_disabled": True}})
    result = post["result"]
    target = result["chat"]
    if target.get("username") and target.get("type") in {"supergroup", "channel"}:
        return f"https://t.me/{target['username']}/{result['message_id']}", True, None
    if target.get("type") in {"supergroup", "channel"} and str(target["id"]).startswith("-100"):
        return f"https://t.me/c/{str(target['id'])[4:]}/{result['message_id']}", False, None
    return None, False, "Telegram does not expose a message permalink for this chat type"
