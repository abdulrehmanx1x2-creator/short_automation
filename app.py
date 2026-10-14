import os, io, json, uuid, sqlite3, shutil, subprocess, mimetypes, requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from cryptography.fernet import Fernet, InvalidToken
from apscheduler.schedulers.background import BackgroundScheduler
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request as GoogleRequest

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
DB_PATH = DATA_DIR / "app.sqlite3"
for p in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR):
    p.mkdir(parents=True, exist_ok=True)

SESSION_SECRET = os.getenv("SESSION_SECRET", "")
FERNET_KEY = os.getenv("FERNET_KEY", "")
CLIENT_SECRETS = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
scheduler = BackgroundScheduler(daemon=True)
app = FastAPI(title="All-in-One Shorts Automation")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or "INSECURE-DEV-ONLY-change-this-secret")
templates = Jinja2Templates(directory=str(BASE / "templates"))

def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS channels(
            id TEXT PRIMARY KEY, label TEXT NOT NULL, email TEXT, token_blob BLOB NOT NULL,
            created_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, channel_id TEXT, video_path TEXT NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL, tags TEXT NOT NULL,
            publish_at TEXT, status TEXT NOT NULL, youtube_video_id TEXT,
            error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS media(
            id TEXT PRIMARY KEY, path TEXT NOT NULL, original_name TEXT NOT NULL,
            created_at TEXT NOT NULL)""")

def now():
    return datetime.now(timezone.utc).isoformat()

def cipher():
    if not FERNET_KEY:
        raise RuntimeError("FERNET_KEY خالی ہے۔ .env میں Fernet key شامل کریں۔")
    try:
        return Fernet(FERNET_KEY.encode())
    except Exception as e:
        raise RuntimeError("FERNET_KEY درست نہیں۔ README میں دی گئی کمانڈ سے key بنائیں۔") from e

def require_ffmpeg():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg نہیں ملا۔ اسے انسٹال کرکے PATH میں شامل کریں۔")

def clean_name(name: str) -> str:
    # Keep only a safe display basename; never use user-supplied paths.
    return Path(name or "image.jpg").name[:180]

def suggestions(topic: str):
    topic = topic.lower()
    if topic == "islamic":
        return ("Beautiful Islamic Reminder #Shorts",
                "A short Islamic reminder. Please verify the source and use only audio you have permission to use.",
                ["Islamic", "Reminder", "Shorts"])
    if topic in ("ronaldo", "football"):
        return ("Ronaldo Football Moments #Shorts",
                "Football edit featuring selected images. Use only media and music you have rights to.",
                ["Ronaldo", "Football", "Shorts"])
    return ("Photo Story #Shorts", "A short photo story made from selected images.", ["Shorts", "PhotoStory"])

def render_slideshow(image_paths, out_path):
    require_ffmpeg()
    if len(image_paths) != 6:
        raise ValueError("بالکل 6 تصاویر منتخب کریں۔")
    # Scale/crop each still to 1080x1920; fade-in/out on each 2s segment.
    # Concatenation preserves a 12-second timeline; fades are transition-like, not a full crossfade.
    cmd = ["ffmpeg", "-y"]
    for p in image_paths:
        cmd += ["-loop", "1", "-t", "2", "-i", str(p)]
    filters = []
    labels = []
    for i in range(6):
        filters.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,setsar=1,fps=30,format=yuv420p,"
            f"fade=t=in:st=0:d=0.18,fade=t=out:st=1.82:d=0.18[v{i}]"
        )
        labels.append(f"[v{i}]")
    filters.append("".join(labels) + "concat=n=6:v=1:a=0[outv]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[outv]",
            "-t", "12", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-movflags", "+faststart", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError("FFmpeg error: " + (result.stderr[-1200:] or "unknown error"))
    return out_path

def credentials_from_channel(channel_id):
    if not FERNET_KEY:
        raise RuntimeError("FERNET_KEY configure نہیں ہے۔")
    with db() as con:
        row = con.execute("SELECT token_blob FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not row:
        raise ValueError("منتخب چینل نہیں ملا۔")
    raw = cipher().decrypt(row["token_blob"])
    data = json.loads(raw.decode())
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_credentials(channel_id, creds)
    return creds

def save_credentials(channel_id, creds):
    payload = creds.to_json().encode()
    blob = cipher().encrypt(payload)
    with db() as con:
        con.execute("UPDATE channels SET token_blob=? WHERE id=?", (blob, channel_id))

def upload_job(job_id):
    with db() as con:
        job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return
    update_job(job_id, status="uploading", error=None)
    try:
        creds = credentials_from_channel(job["channel_id"])
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        body = {
            "snippet": {
                "title": job["title"][:100],
                "description": job["description"][:5000],
                "tags": json.loads(job["tags"]),
                "categoryId": "22"
            },
            "status": {"privacyStatus": "private"}
        }
        media = MediaFileUpload(job["video_path"], mimetype="video/mp4",
                                resumable=True, chunksize=8 * 1024 * 1024)
        response = youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        update_job(job_id, status="uploaded_private", youtube_video_id=response.get("id"), error=None)
    except Exception as e:
        update_job(job_id, status="failed", error=str(e)[:1500])

def update_job(job_id, **kwargs):
    allowed = {"status", "youtube_video_id", "error"}
    kwargs = {k:v for k,v in kwargs.items() if k in allowed}
    kwargs["updated_at"] = now()
    setters = ", ".join(f"{k}=?" for k in kwargs)
    with db() as con:
        con.execute(f"UPDATE jobs SET {setters} WHERE id=?", (*kwargs.values(), job_id))

def schedule_due_job(job_id):
    with db() as con:
        row = con.execute("SELECT publish_at,status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row and row["status"] == "scheduled":
        upload_job(job_id)


def pexels_query_for_topic(topic: str) -> str:
    cleaned = (topic or "").strip()[:100]
    if not cleaned:
        raise ValueError("موضوع لکھیں، مثلاً Islamic یا Ronaldo۔")
    low = cleaned.lower()
    presets = {
        "islamic": "Islamic mosque architecture",
        "islam": "Islamic mosque architecture",
        "ronaldo": "Cristiano Ronaldo football",
        "football": "football soccer stadium",
        "nature": "nature landscape"
    }
    return presets.get(low, cleaned)

def fetch_pexels_images(topic: str, dest_dir: Path):
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY موجود نہیں۔ .env میں Pexels API key شامل کریں۔")
    query = pexels_query_for_topic(topic)
    response = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "per_page": 30, "orientation": "portrait"},
        timeout=30
    )
    if response.status_code == 401:
        raise RuntimeError("Pexels API key غلط یا غیر فعال ہے۔")
    response.raise_for_status()
    photos = response.json().get("photos", [])
    if len(photos) < 6:
        # Try without portrait filter if the first search is too narrow.
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": 30},
            timeout=30
        )
        response.raise_for_status()
        photos = response.json().get("photos", [])
    if len(photos) < 6:
        raise RuntimeError("اس موضوع کے لیے 6 مناسب تصاویر نہیں ملیں۔ دوسرا موضوع آزمائیں۔")
    # Use distinct photos; Pexels API content is not guaranteed to include a specific celebrity.
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths, credits = [], []
    for index, photo in enumerate(photos[:6]):
        src = photo.get("src", {})
        image_url = src.get("portrait") or src.get("large2x") or src.get("large")
        if not image_url:
            continue
        r = requests.get(image_url, timeout=45)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            continue
        ext = ".jpg"
        if "png" in content_type: ext = ".png"
        elif "webp" in content_type: ext = ".webp"
        path = dest_dir / f"{index+1}{ext}"
        path.write_bytes(r.content)
        paths.append(path)
        photographer = photo.get("photographer", "Pexels photographer")
        page_url = photo.get("url", "https://www.pexels.com")
        credits.append(f"Photo by {photographer}: {page_url}")
        if len(paths) == 6:
            break
    if len(paths) != 6:
        raise RuntimeError("Pexels سے چھ تصاویر ڈاؤن لوڈ نہیں ہو سکیں۔ دوبارہ کوشش کریں۔")
    return paths, credits, query

@app.on_event("startup")
def startup():
    init_db()
    if not scheduler.running:
        scheduler.start()
    with db() as con:
        rows = con.execute("SELECT id,publish_at FROM jobs WHERE status='scheduled'").fetchall()
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["publish_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            scheduler.add_job(schedule_due_job, "date", run_date=dt, args=[r["id"]],
                              id=r["id"], replace_existing=True)
        except Exception:
            pass

@app.on_event("shutdown")
def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db() as con:
        channels = con.execute("SELECT id,label,email,created_at FROM channels ORDER BY created_at DESC").fetchall()
        jobs = con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 30").fetchall()
    return templates.TemplateResponse("index.html", {
        "request": request, "channels": channels, "jobs": jobs,
        "base_url": BASE_URL, "ffmpeg": bool(shutil.which("ffmpeg"))
    })

@app.get("/health")
def health():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg"))}

@app.get("/oauth/connect")
def oauth_connect(request: Request):
    if not Path(CLIENT_SECRETS).exists():
        raise HTTPException(400, f"{CLIENT_SECRETS} نہیں ملا۔ README کے مطابق Google OAuth client file رکھیں۔")
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS, scopes=SCOPES)
    flow.redirect_uri = f"{BASE_URL}/oauth/callback"
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true",
        prompt="consent", state=uuid.uuid4().hex
    )
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = flow.code_verifier
    return RedirectResponse(auth_url)

@app.get("/oauth/callback")
def oauth_callback(request: Request, state: str, code: str):
    expected = request.session.get("oauth_state")
    if not expected or state != expected:
        raise HTTPException(400, "OAuth state match نہیں ہوا؛ دوبارہ connect کریں۔")
    if not Path(CLIENT_SECRETS).exists():
        raise HTTPException(400, "OAuth client secrets فائل نہیں ملی۔")
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS, scopes=SCOPES,
                                         state=state, code_verifier=request.session.get("oauth_code_verifier"))
    flow.redirect_uri = f"{BASE_URL}/oauth/callback"
    flow.fetch_token(code=code)
    creds = flow.credentials
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    data = youtube.channels().list(part="snippet", mine=True).execute()
    items = data.get("items", [])
    if not items:
        raise HTTPException(400, "اس Google اکاؤنٹ کے ساتھ YouTube channel نہیں ملا۔")
    ch = items[0]
    channel_id = ch["id"]
    label = ch["snippet"].get("title", "YouTube channel")
    # Store only encrypted OAuth credentials; never expose tokens to the browser.
    payload = creds.to_json().encode()
    blob = cipher().encrypt(payload)
    with db() as con:
        con.execute("""INSERT INTO channels(id,label,email,token_blob,created_at)
          VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,token_blob=excluded.token_blob""",
          (channel_id, label, "", blob, now()))
    request.session.pop("oauth_state", None)
    request.session.pop("oauth_code_verifier", None)
    return RedirectResponse("/?connected=1", status_code=303)

@app.post("/render")
async def render(images: list[UploadFile] = File(...), topic: str = Form("other")):
    if len(images) != 6:
        raise HTTPException(400, "بالکل 6 تصاویر منتخب کریں۔")
    job_id = uuid.uuid4().hex
    work_dir = UPLOAD_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for i, image in enumerate(images):
            if not (image.content_type or "").startswith("image/"):
                raise HTTPException(400, "صرف image files منتخب کریں۔")
            ext = Path(clean_name(image.filename)).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                ext = ".jpg"
            dest = work_dir / f"{i+1}{ext}"
            content = await image.read()
            if len(content) > 20 * 1024 * 1024:
                raise HTTPException(413, "ہر تصویر 20MB سے کم ہونی چاہیے۔")
            dest.write_bytes(content)
            paths.append(dest)
        output = OUTPUT_DIR / f"{job_id}.mp4"
        await __import__("asyncio").to_thread(render_slideshow, paths, output)
        title, description, tags = suggestions(topic)
        with db() as con:
            con.execute("INSERT INTO media(id,path,original_name,created_at) VALUES(?,?,?,?)",
                        (job_id, str(output), "Shorts slideshow", now()))
        return {"id": job_id, "video_url": f"/video/{job_id}", "download_url": f"/download/{job_id}",
                "title": title, "description": description, "tags": tags,
                "message": "ویڈیو تیار ہے؛ اپلوڈ سے پہلے preview ضرور دیکھیں۔"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:1500])
    finally:
        for im in images:
            await im.close()


@app.post("/render-topic")
def render_from_topic(topic: str = Form(...)):
    """Fetch six topic-related Pexels photos and render a 12-second Short."""
    job_id = uuid.uuid4().hex
    work_dir = UPLOAD_DIR / job_id
    try:
        paths, credits, query = fetch_pexels_images(topic, work_dir)
        output = OUTPUT_DIR / f"{job_id}.mp4"
        render_slideshow(paths, output)
        title, description, tags = suggestions(topic.lower())
        credit_text = "\n\nPhotos via Pexels:\n" + "\n".join(credits) + "\nSource: https://www.pexels.com/"
        description = (description + credit_text)[:5000]
        with db() as con:
            con.execute("INSERT INTO media(id,path,original_name,created_at) VALUES(?,?,?,?)",
                        (job_id, str(output), f"Auto images: {topic}", now()))
        return JSONResponse({
            "id": job_id, "video_url": f"/video/{job_id}", "download_url": f"/download/{job_id}",
            "title": title, "description": description, "tags": tags,
            "message": f"موضوع '{topic}' کے لیے Pexels سے 6 تصاویر لے کر ویڈیو تیار ہے۔ سرچ: {query}",
            "credits": credits
        })
    except Exception as e:
        raise HTTPException(500, str(e)[:1500])

@app.get("/video/{media_id}")
def preview_video(media_id: str):
    with db() as con:
        row = con.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
    if not row or not Path(row["path"]).exists():
        raise HTTPException(404, "ویڈیو نہیں ملی۔")
    return FileResponse(row["path"], media_type="video/mp4")

@app.get("/download/{media_id}")
def download_video(media_id: str):
    with db() as con:
        row = con.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
    if not row or not Path(row["path"]).exists():
        raise HTTPException(404, "ویڈیو نہیں ملی۔")
    return FileResponse(row["path"], media_type="video/mp4", filename=f"shorts-{media_id}.mp4")

@app.post("/upload")
def upload_now(channel_id: str = Form(...), media_id: str = Form(...),
               title: str = Form(...), description: str = Form(""), tags: str = Form("Shorts")):
    with db() as con:
        media = con.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
        channel = con.execute("SELECT id FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not media or not Path(media["path"]).exists():
        raise HTTPException(404, "ویڈیو نہیں ملی۔")
    if not channel:
        raise HTTPException(400, "پہلے YouTube چینل connect کریں۔")
    tag_list = [t.strip().lstrip("#") for t in tags.replace("\n", ",").split(",") if t.strip()]
    job_id = uuid.uuid4().hex
    with db() as con:
        con.execute("""INSERT INTO jobs(id,channel_id,video_path,title,description,tags,publish_at,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (job_id, channel_id, media["path"], title[:100], description[:5000],
           json.dumps(tag_list[:30]), None, "queued", now(), now()))
    scheduler.add_job(upload_job, "date", run_date=datetime.now(), args=[job_id], id=job_id, replace_existing=True)
    return RedirectResponse("/", status_code=303)

@app.post("/schedule")
def schedule_upload(channel_id: str = Form(...), media_id: str = Form(...),
                    title: str = Form(...), description: str = Form(""),
                    tags: str = Form("Shorts"), publish_at: str = Form(...)):
    with db() as con:
        media = con.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
        channel = con.execute("SELECT id FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not media or not Path(media["path"]).exists():
        raise HTTPException(404, "ویڈیو نہیں ملی۔")
    if not channel:
        raise HTTPException(400, "پہلے YouTube چینل connect کریں۔")
    try:
        dt = datetime.fromisoformat(publish_at)
        if dt.tzinfo is None:
            # Browser datetime-local is interpreted as server local time; normalize to UTC.
            dt = dt.astimezone()
        dt = dt.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(400, "تاریخ/وقت درست نہیں۔")
    if dt <= datetime.now(timezone.utc):
        raise HTTPException(400, "مستقبل کا وقت منتخب کریں۔")
    tag_list = [t.strip().lstrip("#") for t in tags.replace("\n", ",").split(",") if t.strip()]
    job_id = uuid.uuid4().hex
    with db() as con:
        con.execute("""INSERT INTO jobs(id,channel_id,video_path,title,description,tags,publish_at,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (job_id, channel_id, media["path"], title[:100], description[:5000],
           json.dumps(tag_list[:30]), dt.isoformat(), "scheduled", now(), now()))
    scheduler.add_job(schedule_due_job, "date", run_date=dt, args=[job_id], id=job_id, replace_existing=True)
    return RedirectResponse("/", status_code=303)

@app.post("/retry/{job_id}")
def retry_job(job_id: str):
    with db() as con:
        row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["status"] != "failed":
        raise HTTPException(400, "صرف failed job دوبارہ چلایا جا سکتا ہے۔")
    scheduler.add_job(upload_job, "date", run_date=datetime.now(), args=[job_id],
                      id=f"retry-{job_id}-{uuid.uuid4().hex[:6]}", replace_existing=True)
    return RedirectResponse("/", status_code=303)
