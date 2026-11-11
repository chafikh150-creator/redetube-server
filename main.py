
import ipaddress
import logging
import os
import re
import time
from collections import defaultdict, deque
from urllib.parse import urlencode, urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl


APP_VERSION = "2.2.0"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://redetube-server.onrender.com"
).rstrip("/")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("redetube")


app = FastAPI(
    title="Redetube Server",
    description="Video analysis and download API",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

MAX_URL_LENGTH = 2048
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_request_log = defaultdict(deque)


@app.middleware("http")
async def limit_requests(request: Request, call_next):
    """Very small in-memory rate limit per client address (protects against abuse)."""
    if request.url.path in ("/analyze", "/download"):
        forwarded = request.headers.get("x-forwarded-for", "")
        client = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
        now = time.time()
        bucket = _request_log[client]
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_REQUESTS:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "Too many requests. Please wait a minute."})
        bucket.append(now)
        if len(_request_log) > 5000:
            for key in list(_request_log.keys())[:1000]:
                _request_log.pop(key, None)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # the Android app does not need browser CORS access
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


PLATFORM_NAMES = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "x": "X",
}


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class YtDlpLogger:
    def debug(self, message):
        logger.info("yt-dlp: %s", message)

    def warning(self, message):
        logger.warning("yt-dlp: %s", message)

    def error(self, message):
        logger.error("yt-dlp: %s", message)


def detect_platform(url: str):
    parsed = urlparse(url)

    if parsed.scheme.lower() != "https":
        return None

    if parsed.username or parsed.password:
        return None

    host = (parsed.hostname or "").lower()

    for platform, domains in PLATFORMS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return platform

    return None


def validate_platform_url(url: str):
    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail="Unsupported or invalid video URL. HTTPS is required.",
        )

    return platform


def is_public_host(host: str) -> bool:
    host = (host or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localdomain", ".lan", ".home")):
        return False
    try:
        address = ipaddress.ip_address(host)
        return not (address.is_private or address.is_loopback or address.is_link_local
                    or address.is_reserved or address.is_multicast or address.is_unspecified)
    except ValueError:
        return True


def is_direct_mp4(url: str):
    parsed = urlparse(url)
    if not is_public_host(parsed.hostname):
        return False

    return (
        parsed.scheme.lower() == "https"
        and not parsed.username
        and not parsed.password
        and parsed.path.lower().endswith(".mp4")
    )


def extract_video_info(url: str):
    options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "cachedir": False,
        "logger": YtDlpLogger(),
    }

    logger.info(
        "Starting yt-dlp extraction. Host: %s",
        urlparse(url).hostname,
    )

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=False,
        )

    if not info:
        raise ValueError("yt-dlp returned empty video information")

    return info


def has_audio_and_video(item: dict):
    video_codec = item.get("vcodec")
    audio_codec = item.get("acodec")

    return (
        bool(video_codec)
        and video_codec != "none"
        and bool(audio_codec)
        and audio_codec != "none"
        and bool(item.get("url"))
    )


def make_format_list(info: dict):
    formats = info.get("formats") or []
    available = []

    source_url = (
        info.get("webpage_url")
        or info.get("original_url")
        or ""
    )

    if not source_url:
        logger.warning("Video information has no webpage URL")

    for item in formats:
        if not has_audio_and_video(item):
            continue

        format_id = item.get("format_id")

        if not format_id:
            continue

        protocol = item.get("protocol") or ""

        if protocol not in ("http", "https"):
            continue

        height = item.get("height")
        width = item.get("width")
        ext = item.get("ext") or "unknown"

        if height:
            resolution = f"{height}p"
        elif width:
            resolution = f"{width}px"
        else:
            resolution = item.get("format_note") or "Original"

        file_size = (
            item.get("filesize")
            or item.get("filesize_approx")
        )

        download_query = urlencode({
            "url": source_url,
            "format_id": str(format_id),
        })

        download_url = (
            f"{PUBLIC_BASE_URL}/download?{download_query}"
        )

        available.append({
            "format_id": str(format_id),
            "ext": ext,
            "resolution": resolution,
            "height": height,
            "width": width,
            "filesize": file_size,
            "has_audio": True,
            "has_video": True,
            "download_url": download_url,
        })

    available.sort(
        key=lambda item: item.get("height") or 0,
        reverse=True,
    )

    return available[:12]


@app.get("/")
def root():
    return {
        "name": "Redetube Server",
        "version": APP_VERSION,
        "status": "online",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Redetube Server",
        "version": APP_VERSION,
        "yt_dlp_version": yt_dlp.version.__version__,
    }


@app.post("/analyze")
def analyze(data: AnalyzeRequest, request: Request):
    url = str(data.url)
    if len(url) > MAX_URL_LENGTH:
        raise HTTPException(status_code=400, detail="URL too long.")

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
            "formats": [],
            "message": "Direct MP4 video is ready to download.",
        }

    platform = validate_platform_url(url)

    try:
        info = extract_video_info(url)

        formats = make_format_list(info)

        title = info.get("title")

        author = (
            info.get("uploader")
            or info.get("channel")
            or info.get("creator")
        )

        thumbnail = info.get("thumbnail")

        logger.info(
            "Extraction successful. Platform: %s, title available: %s, formats: %s",
            platform,
            bool(title),
            len(formats),
        )

        return {
            "status": "success",
            "platform": PLATFORM_NAMES[platform],
            "url": url,
            "title": title,
            "author": author,
            "thumbnail_url": thumbnail,
            "duration": info.get("duration"),
            "metadata_available": bool(title or thumbnail),
            "download_available": len(formats) > 0,
            "download_type": "yt_dlp" if formats else None,
            "download_url": (
                formats[0]["download_url"] if formats else None
            ),
            "formats": formats,
            "message": (
                "Video information and download formats retrieved."
                if formats
                else "Video information retrieved, but no compatible "
                     "progressive download format was found."
            ),
        }

    except Exception as error:
        logger.exception(
            "Analysis failed. Platform: %s, host: %s, error: %s",
            platform,
            urlparse(url).hostname,
            error,
        )

        return {
            "status": "recognized",
            "platform": PLATFORM_NAMES[platform],
            "url": url,
            "title": None,
            "author": None,
            "thumbnail_url": None,
            "metadata_available": False,
            "download_available": False,
            "download_type": None,
            "download_url": None,
            "formats": [],
            "error_code": "extraction_failed",
            "message": (
                "The platform was recognized, but video extraction "
                "failed. Check the server logs for details."
            ),
        }


@app.get("/download")
def download(
    url: HttpUrl = Query(...),
    format_id: str = Query(..., max_length=40),
):
    video_url = str(url)
    if len(video_url) > MAX_URL_LENGTH or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,40}", format_id):
        raise HTTPException(status_code=400, detail="Invalid request.")
    platform = validate_platform_url(video_url)

    try:
        info = extract_video_info(video_url)

        formats = info.get("formats") or []

        selected = next(
            (
                item for item in formats
                if str(item.get("format_id")) == format_id
                and has_audio_and_video(item)
                and item.get("protocol") in ("http", "https")
            ),
            None,
        )

        if not selected:
            raise HTTPException(
                status_code=404,
                detail="The requested download format is unavailable.",
            )

        media_url = selected.get("url")

        if not media_url:
            raise HTTPException(
                status_code=404,
                detail="Media URL is unavailable.",
            )

        logger.info(
            "Redirecting download. Platform: %s, format: %s",
            platform,
            format_id,
        )

        return RedirectResponse(
            url=media_url,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    except HTTPException:
        raise

    except Exception as error:
        logger.exception(
            "Download failed. Platform: %s, host: %s, error: %s",
            platform,
            urlparse(video_url).hostname,
            error,
        )

        raise HTTPException(
            status_code=502,
            detail="The platform could not provide this video. Please try again later.",
        )
