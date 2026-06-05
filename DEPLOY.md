# NOVAC — Deployment Guide (Railway + Vercel + MongoDB Atlas)

This deploys the app in three pieces:

| Piece | Host | Notes |
|-------|------|-------|
| FastAPI backend | **Railway** (Docker) | needs ~2 GB RAM for torch + MiniLM |
| React frontend | **Vercel** | static build, free |
| MongoDB | **MongoDB Atlas** | free M0 cluster |

Voice (Whisper) and image OCR (PaddleOCR) are **off by default** to keep the image small.
The core RAG chat works without them. See the last section to turn them on.

---

## 1. MongoDB Atlas (database)

1. Create a free account at <https://www.mongodb.com/cloud/atlas> and create a **free M0 cluster**.
2. **Database Access** → add a database user (username + a strong password). Save them.
3. **Network Access** → Add IP Address → **Allow access from anywhere** (`0.0.0.0/0`).
   Railway's egress IPs are dynamic, so this is the simple option — keep the DB password strong.
4. **Connect** → **Drivers** → copy the connection string. It looks like:
   ```
   mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
   Replace `<user>`/`<password>` with the ones from step 2. This is your `MONGO_URI`.

> The fresh cluster is empty. The admin/user logins are auto-created on first boot, but the
> document corpus starts empty — you'll re-upload your PDFs through the admin UI after deploy.

---

## 2. Railway (backend)

1. Sign in at <https://railway.app> with GitHub.
2. **New Project** → **Deploy from GitHub repo** → pick `DiveshK007/NOVAC_Chatbot`.
3. Open the service → **Settings**:
   - **Root Directory**: `Task-1 WebAPI`  ← note the space; type it exactly.
   - Railway auto-detects the `Dockerfile` (no start command needed; it binds `$PORT`).
4. **Variables** → add:

   | Variable | Value |
   |----------|-------|
   | `MONGO_URI` | the Atlas string from step 1.4 |
   | `JWT_SECRET` | a long random string (e.g. `openssl rand -hex 32`) — **don't skip this** |
   | `MISTRAL_API_KEY` | your key |
   | `GROQ_API_KEY` | your key (needed for the math/calc features) |
   | `XAI_API_KEY` | optional (Grok provider) |
   | `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | optional (text-to-speech) |
   | `FRONTEND_ORIGINS` | set later, after Vercel (step 4) |

5. **Deploy.** First build takes ~10 min (torch + model bake). When done, go to
   **Settings → Networking → Generate Domain**. You get a URL like
   `https://novac-backend.up.railway.app`. Open `/` — it should return
   `{"message": "FastAPI Backend Running"}`.

---

## 3. Vercel (frontend)

1. Sign in at <https://vercel.com> with GitHub → **Add New → Project** → import `NOVAC_Chatbot`.
2. **Root Directory**: `Task-1 WebAPI/frontend`. Framework preset auto-detects **Vite**.
3. **Environment Variables** → add:

   | Variable | Value |
   |----------|-------|
   | `VITE_API_URL` | your Railway backend URL from step 2.5 (no trailing slash) |

4. **Deploy.** You get a URL like `https://novac.vercel.app`.

---

## 4. Connect them (CORS)

1. Back in **Railway → Variables**, set:
   ```
   FRONTEND_ORIGINS = https://novac.vercel.app
   ```
   (your real Vercel URL; comma-separate if you have several, no trailing slash).
2. Railway redeploys. The backend now accepts requests from your frontend.

Done. Visit the Vercel URL and log in with `admin@novac.com` / `admin`
(change this in production). Upload your documents via the admin UI to rebuild the corpus.

---

## 5. Optional: enable Voice + Image OCR

These need much more RAM and a far bigger image, so they're off by default.

1. In the `Dockerfile`, after `RUN pip install -r requirements.txt`, also install the extras
   and (for voice) ffmpeg:
   ```dockerfile
   RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
   COPY requirements-optional.txt .
   RUN pip install -r requirements-optional.txt
   ```
2. On Railway set `ENABLE_OCR=true` and/or `ENABLE_VOICE=true`, and bump the instance RAM.

---

## Troubleshooting

- **CORS error in browser console** → `FRONTEND_ORIGINS` doesn't exactly match the Vercel URL
  (scheme + host, no trailing slash). Fix and redeploy the backend.
- **Login/API calls hit localhost** → `VITE_API_URL` wasn't set at Vercel build time; set it and redeploy.
- **502 right after deploy** → the container is still loading models; give it ~20–30s.
- **Mongo connection timeout** → Atlas Network Access doesn't allow `0.0.0.0/0`, or the
  password in `MONGO_URI` is wrong/unescaped (URL-encode special characters).
- **Math answers stop computing** → Groq's free-tier daily token limit; wait for reset or upgrade.
