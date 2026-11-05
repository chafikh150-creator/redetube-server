import logging
from urllib.parse import urlencode, urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl


APP_VERSION = "2.1.0"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("redetube")

app = FastAPI(
    title="Redetube Server",
    description="Video analysis and download API",
    version=APP_VERSION,
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

PLATFORM_NAMES = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "x": "X",
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


def extract_video_info(url: str):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        return downloader.extract_info(url, download=False)


def has_audio_and_video(item: dict):
    video_codec = item.get("vcodec")
    audio_codec = item.get("acodec")

    return (
        video_codec
        and video_codec != "none"
        and audio_codec
        and audio_codec != "none"
        and item.get("url")
    )


def make_format_list(info: dict, request: Request):
    formats = info.get("formats") or []
    available = []

    for item in formats:
        if not has_audio_and_video(item):
            continue

        format_id = item.get("format_id")
        if not format_id:
            continue

        height = item.get("height")
        width = item.get("width")
        ext = item.get("ext") or "mp4"

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
            "url": info.get("webpage_url") or info.get("original_url"),
            "format_id": format_id,
        })

        base_url = str(request.base_url).rstrip("/")
        download_url = f"{base_url}/download?{download_query}"

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
    }


@app.post("/analyze")
def analyze(data: AnalyzeRequest, request: Request):
    url = str(data.url)

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

    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail="Unsupported video platform.",
        )

    try:
        info = extract_video_info(url)

        if not info:
            raise ValueError("No video information returned")

        formats = make_format_list(info, request)

        title = info.get("title")
        author = (
            info.get("uploader")
            or info.get("channel")
            or info.get("creator")
        )

        thumbnail = info.get("thumbnail")

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
        logger.warning(
            "Analysis failed for %s: %s",
            platform,
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
            "message": (
                "The platform was recognized, but its video could not "
                "be accessed. The post may be private, restricted, "
                "or temporarily unavailable."
            ),
        }


@app.get("/download")
def download(
    url: str = Query(...),
    format_id: str = Query(...),
):
    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail="Unsupported video platform.",
        )

    try:
        info = extract_video_info(url)

        formats = info.get("formats") or []

        selected = next(
            (
                item for item in formats
                if str(item.get("format_id")) == format_id
                and has_audio_and_video(item)
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

        return RedirectResponse(
            url=media_url,
            status_code=302,
        )

    except HTTPException:
        raise

    except Exception as error:
        logger.warning("Download failed: %s", error)

        raise HTTPException(
            status_code=502,
            detail=(
                "The platform could not provide this video. "
                "Please try again later."
            ),
        )
