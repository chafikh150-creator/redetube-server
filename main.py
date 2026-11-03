
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json

app = FastAPI(
    title="Redetube Server",
    description="Backend API for Redetube",
    version="1.2.0"
)

PLATFORMS = {
    "YouTube": ["youtube.com", "youtu.be"],
    "Instagram": ["instagram.com"],
    "Facebook": ["facebook.com", "fb.watch"],
    "TikTok": ["tiktok.com"],
    "X": ["x.com", "twitter.com"]
}

OEMBED_ENDPOINTS = {
    "YouTube": "https://www.youtube.com/oembed",
    "TikTok": "https://www.tiktok.com/oembed",
    "X": "https://publish.twitter.com/oembed"
}

REQUEST_TIMEOUT = 12
MAX_RESPONSE_SIZE = 1_000_000


class AnalyzeRequest(BaseModel):
    url: HttpUrl


def detect_platform(url: str):
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()

    if not host or parsed.scheme not in ("http", "https"):
        return None

    for platform, domains in PLATFORMS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return platform

    return None


def fetch_oembed(platform: str, video_url: str):
    endpoint = OEMBED_ENDPOINTS.get(platform)

    if not endpoint:
        return None

    query = urlencode({
        "url": video_url,
        "format": "json"
    })

    request = Request(
        f"{endpoint}?{query}",
        headers={
            "User-Agent": "Redetube/1.2 (Android; compatible)",
            "Accept": "application/json"
        },
        method="GET"
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                return None

            content = response.read(MAX_RESPONSE_SIZE + 1)

            if len(content) > MAX_RESPONSE_SIZE:
                return None

            data = json.loads(content.decode("utf-8"))

            thumbnail = data.get("thumbnail_url")
            thumbnail_host = (
                urlparse(thumbnail).hostname or ""
            ).lower() if thumbnail else ""

            # Only return secure thumbnail URLs.
            if thumbnail and not (
                urlparse(thumbnail).scheme == "https"
                and thumbnail_host
            ):
                thumbnail = None

            return {
                "title": data.get("title"),
                "author": data.get("author_name"),
                "thumbnail_url": thumbnail,
                "provider": data.get("provider_name"),
                "metadata_available": True
            }

    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


@app.get("/")
def home():
    return {
        "app": "Redetube",
        "status": "online",
        "version": "1.2.0",
        "message": "Redetube server is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Redetube Server",
        "version": "1.2.0"
    }


@app.post("/analyze")
def analyze_link(request: AnalyzeRequest):
    url = str(request.url)
    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported platform. Supported: "
                "YouTube, Instagram, Facebook, TikTok and X."
            )
        )

    metadata = fetch_oembed(platform, url)

    if metadata:
        return {
            "status": "success",
            "platform": platform,
            "url": url,
            "metadata_available": True,
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "thumbnail_url": metadata.get("thumbnail_url"),
            "provider": metadata.get("provider"),
            "message": "Video information retrieved successfully."
        }

    if platform in ("Instagram", "Facebook"):
        message = (
            "Platform recognized. Official API access may be "
            "required to retrieve video information."
        )
    else:
        message = (
            "Platform recognized, but public video information "
            "could not be retrieved. The content may be private, "
            "unavailable, or unsupported by the provider."
        )

    return {
        "status": "recognized",
        "platform": platform,
        "url": url,
        "metadata_available": False,
        "title": None,
        "author": None,
        "thumbnail_url": None,
        "provider": platform,
        "message": message
    }
