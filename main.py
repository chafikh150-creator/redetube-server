from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json


app = FastAPI(
    title="Redetube Server",
    description="Backend API for Redetube",
    version="1.4.0"
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


def is_direct_mp4(url: str) -> bool:
    """
    Detect a URL whose path ends in .mp4.
    This identifies a direct MP4 candidate; it does not
    guarantee that the remote server allows downloading it.
    """
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.path.lower().endswith(".mp4")
        )
    except Exception:
        return False


def detect_platform(url: str):
    parsed = urlparse(url)
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
            "User-Agent": "Mozilla/5.0 (compatible; Redetube/1.4)",
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

            if not isinstance(data, dict):
                return None

            title = data.get("title")
            author = data.get("author_name")
            thumbnail = data.get("thumbnail_url")
            provider = data.get("provider_name")

            title = title.strip() if isinstance(title, str) else None
            author = author.strip() if isinstance(author, str) else None
            provider = provider.strip() if isinstance(provider, str) else None

            if not title:
                title = None

            if not author:
                author = None

            if not provider:
                provider = None

            if isinstance(thumbnail, str):
                parsed_thumbnail = urlparse(thumbnail)

                if (
                    parsed_thumbnail.scheme != "https"
                    or not parsed_thumbnail.hostname
                ):
                    thumbnail = None
            else:
                thumbnail = None

            if not any([title, author, thumbnail]):
                return None

            return {
                "title": title,
                "author": author,
                "thumbnail_url": thumbnail,
                "provider": provider or platform,
                "metadata_available": True
            }

    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        ValueError,
        UnicodeDecodeError
    ):
        return None


@app.get("/")
def home():
    return {
        "app": "Redetube",
        "status": "online",
        "version": "1.4.0",
        "message": "Redetube server is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Redetube Server",
        "version": "1.4.0"
    }


@app.post("/analyze")
def analyze_link(request: AnalyzeRequest):

    url = str(request.url)

    # 1. Detect direct MP4 links first.
    if is_direct_mp4(url):
        return {
            "status": "success",
            "platform": "Direct MP4",
            "url": url,
            "metadata_available": False,
            "title": None,
            "author": None,
            "thumbnail_url": None,
            "provider": "Direct video link",
            "download_available": True,
            "download_url": url,
            "download_type": "direct_mp4",
            "message": (
                "Direct MP4 link detected. "
                "The remote server must permit access and downloading."
            )
        }

    # 2. Detect supported social platforms.
    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported link. Supported platforms: "
                "YouTube, Instagram, Facebook, TikTok and X. "
                "Direct HTTPS MP4 links are also supported."
            )
        )

    # 3. Retrieve public metadata through oEmbed.
    metadata = fetch_oembed(platform, url)

    if metadata:
        return {
            "status": "success",
            "platform": platform,
            "url": url,
            "metadata_available": True,
            "title": metadata["title"],
            "author": metadata["author"],
            "thumbnail_url": metadata["thumbnail_url"],
            "provider": metadata["provider"],
            "download_available": False,
            "download_url": None,
            "download_type": None,
            "message": "Video information retrieved successfully."
        }

    # 4. Platform recognized, but metadata unavailable.
    if platform in ("Instagram", "Facebook"):
        message = (
            "Platform recognized. Official API access may be "
            "required to retrieve video information."
        )
    else:
        message = (
            "Platform recognized, but useful video metadata "
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
        "download_available": False,
        "download_url": None,
        "download_type": None,
        "message": message
    }
