# NOVAC — Deployment Guide (Hugging Face Spaces + Vercel + MongoDB Atlas)

A fully **free** stack:

| Piece | Host | Notes |
|-------|------|-------|
| FastAPI backend | **Hugging Face Spaces** (Docker, free CPU) | 16 GB RAM — fits torch + MiniLM |
| React frontend | **Vercel** | static build, free |
| MongoDB | **MongoDB Atlas** | free M0 cluster |

Voice (Whisper) and image OCR (PaddleOCR) are **off by default** to keep the image small.
The core RAG chat works without them. See the last section to turn them on.

> Free HF Spaces are **public** (your backend code is visible — secrets are NOT, they live in
> Space settings) and **sleep after ~48 h idle** (first request after waking takes ~30 s).

---

## 1. MongoDB Atlas (database)

1. Create a free account at <https://www.mongodb.com/cloud/atlas> and a **free M0 cluster**.
2. **Database Access** → add a user (username + strong password). Save them.
3. **Network Access** → Add IP → **Allow access from anywhere** (`0.0.0.0/0`).
4. **Connect → Drivers** → copy the connection string:
   ```
   mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
   Substitute your user/password. This is your `MONGO_URI`.

> The fresh cluster is empty. The admin/user logins are auto-created on first boot, but the
> document corpus starts empty — re-upload your PDFs through the admin UI after deploy.

---

## 2. Hugging Face Space (backend)

1. Create an account at <https://huggingface.co>.
2. **New → Space**. Name it (e.g. `novac-backend`), **SDK: Docker**, **Hardware: CPU basic
   (free, 16 GB)**, visibility Public. Create.
3. Add the backend files to the Space. Easiest is the web UI — in the Space, **Files → Add
   file → Upload files**, and upload these four from `Task-1 WebAPI/`:
   - `Dockerfile`
   - `README.md`  (its YAML header configures the Space — keep it as `README.md`)
   - `requirements.txt`
   - `fast_api.py`

   (Or via git: `git clone https://huggingface.co/spaces/<you>/novac-backend`, copy those
   four files in, `git add . && git commit && git push`.)
4. **Settings → Variables and secrets** → add as **secrets**:

   | Name | Value |
   |------|-------|
   | `MONGO_URI` | the Atlas string from step 1.4 |
   | `JWT_SECRET` | a long random string (e.g. `openssl rand -hex 32`) — **don't skip** |
   | `MISTRAL_API_KEY` | your key |
   | `GROQ_API_KEY` | your key (needed for the math/calc features) |
   | `XAI_API_KEY` | optional (Grok) |
   | `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | optional (text-to-speech) |
   | `FRONTEND_ORIGINS` | set later, after Vercel (step 4) |

5. The Space builds automatically (~10 min the first time: torch + model bake). When it shows
   **Running**, your backend URL is `https://<your-username>-novac-backend.hf.space`.
   Open it — `/` returns `{"message": "FastAPI Backend Running"}`.

---

## 3. Vercel (frontend)

1. Sign in at <https://vercel.com> with GitHub → **Add New → Project** → import `NOVAC_Chatbot`.
2. **Root Directory**: `Task-1 WebAPI/frontend`. Framework auto-detects **Vite**.
3. **Environment Variables** → add:

   | Variable | Value |
   |----------|-------|
   | `VITE_API_URL` | your HF Space URL from step 2.5 (no trailing slash) |

4. **Deploy.** You get a URL like `https://novac.vercel.app`.

---

## 4. Connect them (CORS)

1. Back in the **HF Space → Settings → Variables and secrets**, set:
   ```
   FRONTEND_ORIGINS = https://novac.vercel.app
   ```
   (your real Vercel URL; comma-separate for several; no trailing slash).
2. **Restart** the Space (Settings → Factory reboot, or it restarts on a new variable).
   The backend now accepts requests from your frontend.

Done. Visit the Vercel URL and log in with `admin@novac.com` / `admin`
(change this in production). Upload your documents via the admin UI to rebuild the corpus.

---

## 5. Optional: enable Voice + Image OCR

These need more RAM and a much bigger image, so they're off by default.

1. In the `Dockerfile`, after `RUN pip install -r requirements.txt`, also add:
   ```dockerfile
   RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
   COPY requirements-optional.txt .
   RUN pip install -r requirements-optional.txt
   ```
   (also upload `requirements-optional.txt` to the Space).
2. Add Space secrets `ENABLE_OCR=true` and/or `ENABLE_VOICE=true`.

### Optional: enable the knowledge graph (Neo4j)

The graph is off unless `NEO4J_*` is configured. To turn it on, set these as secrets
(the `neo4j` driver is already in `requirements.txt`):

| Name | Value |
|------|-------|
| `NEO4J_URI` | `neo4j+s://<id>.databases.neo4j.io` (Aura) or `bolt://host:7687` (self-hosted) |
| `NEO4J_USERNAME` | usually `neo4j` |
| `NEO4J_PASSWORD` | your database password |
| `NEO4J_DATABASE` | optional, defaults to `neo4j` |

[Neo4j Aura](https://neo4j.com/cloud/aura-free/) has a free tier. Note: with the graph on,
each uploaded chunk makes an extra LLM call to extract triples — slower uploads and more
tokens (watch the Groq daily limit). Re-upload documents after enabling so the graph fills.

---

## Troubleshooting

- **CORS error in browser console** → `FRONTEND_ORIGINS` doesn't exactly match the Vercel URL
  (scheme + host, no trailing slash). Fix and restart the Space.
- **Login/API calls hit localhost** → `VITE_API_URL` wasn't set at Vercel build time; set it and redeploy.
- **Space stuck building / first request slow** → first build bakes torch + the model (~10 min);
  a slept Space takes ~30 s to wake.
- **Mongo connection timeout** → Atlas Network Access doesn't allow `0.0.0.0/0`, or the
  password in `MONGO_URI` is wrong/unescaped (URL-encode special characters).
- **Math answers stop computing** → Groq's free-tier daily token limit; wait for reset or upgrade.
