# تکنیکی آرکیٹیکچر

## Data flow
Browser (mobile/desktop) -> FastAPI -> Pexels API topic search (or manual image upload) -> image storage -> FFmpeg renderer -> MP4 storage -> SQLite job queue -> APScheduler -> Google OAuth credentials -> YouTube Data API videos.insert

## MVP file tree
- `app.py`: routes, SQLite, OAuth, render, upload, scheduling
- `templates/index.html`: responsive UI
- `requirements.txt`: Python packages
- `Dockerfile`: container image with FFmpeg
- `docker-compose.yml`: local/container run
- `.env.example`: secrets/config template
- `data/`: generated at runtime, contains DB, uploaded images, videos and encrypted OAuth token blobs

## Local + cloud model
- Local: run FastAPI on your PC and access via localhost. Browser on the same device works; phone on same Wi-Fi can use the computer's LAN IP only if you bind to `0.0.0.0` and configure firewall. Do not expose it directly to the public internet.
- Cloud: deploy the same container to a persistent Python host; configure HTTPS, BASE_URL, OAuth redirect URI and persistent storage. The server, not the phone, performs scheduled work.
- Multiple channels: connect each Google account separately. Current MVP assumes one YouTube channel is returned for each Google account authorization; Brand Accounts/channel switching can need extra account selection/implementation.

## Production upgrades before public release
1. Real user authentication and per-user tenant isolation.
2. HTTPS, CSRF protection for all state-changing forms, rate limiting and upload limits.
3. Database migration system and PostgreSQL.
4. Durable queue (Celery/RQ + Redis) instead of in-process APScheduler.
5. Object storage for media and encrypted secrets management.
6. Worker monitoring, retries with backoff, idempotency keys and deletion controls.
7. Explicit audio-rights workflow; no arbitrary trend-music scraping.
8. Automated tests, dependency scanning, privacy policy and Google OAuth verification as required.
