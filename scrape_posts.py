#!/usr/bin/env python3
"""
Scrape the "Posts" (community) tab of YouTube channels and write one Atom feed.

- Reads channels.txt (channel IDs or @handles).
- Fetches https://www.youtube.com/<channel>/posts and parses the embedded
  `ytInitialData` JSON (the same data the page itself renders from).
- YouTube only shows relative times ("35 minutes ago"), so each post gets a
  "first seen" timestamp, stored in state.json. That also means posts that
  scroll off the top of the page stay in your feed.
- Writes feed.xml (Atom). Exits non-zero if any channel could not be fetched
  or parsed, so a broken scraper shows up as a red X in GitHub Actions
  instead of silently producing a stale feed.

This is an unofficial scraper: YouTube can change its page structure at any
time. If it stops finding posts, the log prints diagnostics.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

import requests

FEEDS_DIR = Path("feeds")
CHANNELS_FILE = Path("channels.txt")
STATE_FILE = Path("state.json")
OUTPUT_FILE = Path("feed.xml")
MAX_ITEMS_IN_FEED = 300
FEED_TITLE = "YouTube community posts"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    # Pre-accept the cookie-consent wall (the cause of redirects from EU servers).
    "Cookie": "CONSENT=YES+1; SOCS=CAI",
}

def slugify(text):
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-") or "channel"

def read_channels():
    """Lines look like:  UCxxxx  my-slug   # comment   (slug is optional)."""
    channels = []
    seen_slugs = {}
    for line in CHANNELS_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        target = parts[0]
        slug = slugify(parts[1] if len(parts) > 1 else target.lstrip("@"))
        if slug in seen_slugs:
            sys.exit(f"Duplicate folder name '{slug}' for {target} and {seen_slugs[slug]} "
                     f"- give one of them a different slug in channels.txt")
        seen_slugs[slug] = target
        channels.append((target, slug))
    return channels

def posts_url(channel):
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}/posts"
    return f"https://www.youtube.com/channel/{channel}/posts"


def extract_initial_data(page_html):
    marker = "ytInitialData = "
    start = page_html.find(marker)
    if start == -1:
        return None
    start += len(marker)
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html[start:])
        return data
    except json.JSONDecodeError:
        return None


def walk(obj):
    """Yield every dict and list nested anywhere inside obj."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def find_values(obj, key):
    """All values stored under `key` anywhere inside obj, in document order."""
    found = []

    def rec(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == key:
                    found.append(v)
                rec(v)
        elif isinstance(o, list):
            for v in o:
                rec(v)

    rec(obj)
    return found


def runs_to_text(node):
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def parse_post(renderer):
    post_id = renderer.get("postId")
    if not post_id:
        return None

    images = []
    for thumbs in find_values(renderer.get("backstageAttachment", {}), "thumbnails"):
        if isinstance(thumbs, list) and thumbs and isinstance(thumbs[-1], dict):
            url = thumbs[-1].get("url")
            if url:
                images.append(url)

    video_ids = find_values(renderer.get("backstageAttachment", {}), "videoId")
    has_poll = bool(find_values(renderer.get("backstageAttachment", {}), "pollRenderer"))

    return {
        "id": post_id,
        "author": runs_to_text(renderer.get("authorText")) or "Unknown channel",
        "text": runs_to_text(renderer.get("contentText")),
        "when_text": runs_to_text(renderer.get("publishedTimeText")),
        "link": f"https://www.youtube.com/post/{post_id}",
        "images": images,
        "videos": [f"https://www.youtube.com/watch?v={v}" for v in video_ids if isinstance(v, str)],
        "poll": has_poll,
    }


def find_posts(data):
    posts = []
    seen = set()
    for key in ("backstagePostRenderer", "postRenderer"):
        for renderer in find_values(data, key):
            if not isinstance(renderer, dict):
                continue
            post = parse_post(renderer)
            if post and post["id"] not in seen:
                seen.add(post["id"])
                posts.append(post)
    return posts


def fetch_channel(channel):
    """Return a list of posts (newest first). Raises RuntimeError on failure."""
    url = posts_url(channel)
    resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"{url} -> HTTP {resp.status_code} (final URL: {resp.url})")
    if "consent.youtube.com" in resp.url:
        raise RuntimeError(f"{url} -> redirected to consent page: {resp.url}")

    data = extract_initial_data(resp.text)
    if data is None:
        snippet = resp.text[:300].replace("\n", " ")
        raise RuntimeError(
            f"{url} -> no ytInitialData found (page length {len(resp.text)}). "
            f"Possibly a bot check. Start of page: {snippet!r}"
        )

    posts = find_posts(data)
    if not posts:
        print(
            f"WARNING: {channel}: page parsed but no posts found. "
            f"Either the channel has no posts, or YouTube changed its format. "
            f"Top-level keys: {list(data.keys())}"
        )
    return posts

def load_state(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"posts": {}}

def item_html(p):
    parts = []
    if p["text"]:
        parts.append("<p>" + escape(p["text"]).replace("\n", "<br>") + "</p>")
    for img in p["images"]:
        parts.append(f'<p><img src="{escape(img, quote=True)}"></p>')
    for v in p["videos"]:
        parts.append(f'<p>Video: <a href="{escape(v, quote=True)}">{escape(v)}</a></p>')
    if p.get("poll"):
        parts.append("<p>[poll - open the post to see the options]</p>")
    parts.append(f'<p><a href="{escape(p["link"], quote=True)}">Open post</a></p>')
    return "".join(parts)


def item_title(p):
    text = " ".join(p["text"].split())
    if not text:
        text = "(image/attachment post)" if p["images"] or p["videos"] else "(post)"
    if len(text) > 90:
        text = text[:87] + "..."
    return f'{p["author"]}: {text}'

def write_feed(state, out_path, slug):
    posts = sorted(state["posts"].values(), key=lambda p: p["first_seen"], reverse=True)
    posts = posts[:MAX_ITEMS_IN_FEED]
    title = posts[0]["author"] if posts else slug
    updated = posts[0]["first_seen"] if posts else datetime.now(timezone.utc).isoformat()

    out = ['<?xml version="1.0" encoding="utf-8"?>']
    out.append('<feed xmlns="http://www.w3.org/2005/Atom">')
    out.append(f"<title>{escape(title)} - community posts</title>")
    out.append(f"<id>urn:yt-posts-feed:{escape(slug)}</id>")
    out.append(f"<updated>{escape(updated)}</updated>")
    for p in posts:
        out.append("<entry>")
        out.append(f"<title>{escape(item_title(p))}</title>")
        out.append(f'<link href="{escape(p["link"], quote=True)}"/>')
        out.append(f"<id>{escape(p['link'])}</id>")
        out.append(f"<updated>{escape(p['first_seen'])}</updated>")
        out.append(f"<author><name>{escape(p['author'])}</name></author>")
        out.append(f'<content type="html">{escape(item_html(p))}</content>')
        out.append("</entry>")
    out.append("</feed>")
    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    failures = 0
    now = datetime.now(timezone.utc)

    for channel, slug in read_channels():
        folder = FEEDS_DIR / slug
        state_path = folder / "state.json"
        feed_path = folder / "feed.xml"
        state = load_state(state_path)

        try:
            posts = fetch_channel(channel)
        except Exception as exc:  # noqa: BLE001 - report, keep old files, go on
            failures += 1
            print(f"ERROR: {channel} ({slug}): {exc}", file=sys.stderr)
            continue

        new = 0
        for index, p in enumerate(posts):
            if p["id"] in state["posts"]:
                # refresh content in case the post was edited, keep first_seen
                p["first_seen"] = state["posts"][p["id"]]["first_seen"]
                state["posts"][p["id"]] = p
            else:
                # page order is newest-first; keep that order for same-run posts
                p["first_seen"] = (now - timedelta(seconds=index)).isoformat()
                state["posts"][p["id"]] = p
                new += 1

        folder.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        write_feed(state, feed_path, slug)
        print(f"{slug}: {len(posts)} posts on page, {new} new, {len(state['posts'])} stored in total")

    if failures:
        print(f"{failures} channel(s) failed - see errors above.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
