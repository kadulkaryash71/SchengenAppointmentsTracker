from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.models import SubscribeRequest, SubscribeResponse
from api.store import add_subscriber, find_by_email, list_subscribers

PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"

app = FastAPI(title="Schengen Slot Bot Subscription API")

# TODO: restrict allow_origins to the real landing-page origin once hosting is decided.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# Serves pages/assets/css/*.css etc. at the same absolute paths the HTML already links to,
# so index.html/tos.html need no changes to their <link>/<script> tags.
app.mount("/pages/assets", StaticFiles(directory=PAGES_DIR / "assets"), name="page-assets")


@app.get("/index", response_class=HTMLResponse)
def render_index():
    return (PAGES_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/terms-of-service", response_class=HTMLResponse)
def render_tos():
    return (PAGES_DIR / "tos.html").read_text(encoding="utf-8")


@app.post("/api/subscribe", response_model=SubscribeResponse, status_code=201)
def subscribe(payload: SubscribeRequest):
    if find_by_email(payload.email):
        raise HTTPException(status_code=409, detail="This email is already subscribed.")
    add_subscriber(payload)
    return SubscribeResponse(email=payload.email)


# Debug-only: lets us inspect stored subscribers. Not linked from the frontend.
@app.get("/api/subscribers")
def subscribers():
    return list_subscribers()
