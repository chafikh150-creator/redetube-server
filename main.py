from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from urllib.parse import urlparse

app = FastAPI(
    title="Redetube Server",
    description="Backend API for Redetube",
    version="1.1.0"
)

PLATFORMS = {
    "YouTube": ["youtube.com", "youtu.be"],
    "Instagram": ["instagram.com"],
    "Facebook": ["facebook.com", "fb.watch"],
    "TikTok": ["tiktok.com"],
    "X": ["x.com", "twitter.com"]
}


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


@app.get("/")
def home():
    return {
        "app": "Redetube",
        "status": "online",
        "message": "Redetube server is running"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze")
def analyze_link(request: AnalyzeRequest):
    url = str(request.url)
    platform = detect_platform(url)

    if not platform:
        raise HTTPException(
            status_code=400,
            detail="Unsupported platform. Supported: YouTube, Instagram, Facebook, TikTok and X."
        )

    return {
        "status": "success",
        "platform": platform,
        "url": url,
        "message": "Platform recognized. Video extraction is not enabled yet."
    }
