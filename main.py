from fastapi import FastAPI

app = FastAPI(
    title="Redetube Server",
    description="Backend API for the Redetube application",
    version="1.0.0"
)

@app.get("/")
def home():
    return {
        "app": "Redetube",
        "status": "online",
        "message": "Redetube server is running"
    }

@app.get("/health")
def health():
    return {
        "status": "ok"
    }
