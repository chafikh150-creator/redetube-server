import re
import logging
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

APP_VERSION = "2.0.0"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("redetube")

app = FastAPI(
    title="Redetube Server",
    description="Video analysis API for Redetube",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PLATFORMS = {
    "youtube": ["youtube.com", "youtu.be"],
    "instagram": ["instagram.com"],
    "facebook": ["facebook.com", "fb.watch"],
    "tiktok": ["tiktok.com"],
    "x": ["x.com", "twitter.com"],
}


class AnalyzeRequest(BaseModel):
    url: HttpUrl


def detect_platform(url: str):
    host = (urlparse(url).hostname or "").lower()

    for platform, domains in PLATFORMS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return platform

    return None


def is_direct_mp4(url: str):
    parsed = urlparse(url)

    return (
        parsed.scheme == "https"
        and parsed.path.lower().endswith(".mp4")
    )


def fetch_oembed(endpoint: str):
    try:
        request = Request(
            endpoint,
            headers={
                "User-Agent": "Mozilla/5.0 Redetube/2.0"
            }
        )

        with urlopen(request, timeout=12) as response:
            content = response.read(1024 * 1024)

        import json
        data = json.loads(content.decode("utf-8"))

        return {
            "title": data.get("title"),
            "author": data.get("author_name"),
            "thumbnail_url": data.get("thumbnail_url"),
            "provider": data.get("provider_name"),
        }

    except Exception as error:
        logger.warning("Metadata unavailable: %s", error)
        return None


def get_metadata(url: str, platform: str):
    import urllib.parse

    encoded_url = urllib.parse.quote(url, safe="")

    endpoints = {
        "youtube": (
            "https://www.youtube.com/oembed"
            f"?url={encoded_url}&format=json"
        ),
        "tiktok": (
            "https://www.tiktok.com/oembed?url="
            f"{encoded_url}"
        ),
        "x": (
            "https://publish.twitter.com/oembed?url="
            f"{encoded_url}"
        ),
    }

    endpoint = endpoints.get(platform)

    if not endpoint:
        return None

    return fetch_oembed(endpoint)


@app.get("/")
def root():
    return {
        "name": "Redetube Server",
        "version": APP_VERSION,
        "status": "online"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Redetube Server",
        "version": APP_VERSION
    }


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    url = str(request.url)

    if is_direct_mp4(url):
        return {
            "status": "success",
            "platform": "Direct MP4",
            "url": url,
            "title": "Direct video",
            "author": None,
            "thumbnail_url": None,
            "metadata_available": False,
            "download_available": True,
            "download_type": "direct_mp4",
            "download_url": url,
            "message": "Direct MP4 video is ready to download."
        }

    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail="Unsupported video platform."
        )

    metadata = get_metadata(url, platform)

    platform_names = {
        "youtube": "YouTube",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "tiktok": "TikTok",
        "x": "X"
    }

    if metadata:
        return {
            "status": "success",
            "platform": platform_names[platform],
            "url": url,
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "thumbnail_url": metadata.get("thumbnail_url"),
            "provider": metadata.get("provider"),
            "metadata_available": True,
            "download_available": False,
            "download_type": None,
            "download_url": None,
            "message": (
                "Video information retrieved successfully. "
                "Direct download is not available for this link."
            )
        }

    return {
        "status": "recognized",
        "platform": platform_names[platform],
        "url": url,
        "title": None,
        "author": None,
        "thumbnail_url": None,
        "metadata_available": False,
        "download_available": False,
        "download_type": None,
        "download_url": None,
        "message": (
            "Platform recognized, but video information "
            "could not be retrieved."
        )
    }
