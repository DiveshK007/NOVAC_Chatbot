---
title: NOVAC Backend
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# NOVAC Backend (FastAPI RAG)

Runs as a Hugging Face **Docker Space**. The full deployment guide is in
[`DEPLOY.md`](../DEPLOY.md) at the repo root.

Set these as Space **secrets** (Settings → Variables and secrets):

- `MONGO_URI` — MongoDB Atlas connection string
- `JWT_SECRET` — a long random string
- `MISTRAL_API_KEY`, `GROQ_API_KEY` — LLM providers (Groq powers the math/calc features)
- `FRONTEND_ORIGINS` — your Vercel frontend URL (for CORS), e.g. `https://novac.vercel.app`
- optional: `XAI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`

Voice (Whisper) and image OCR (PaddleOCR) are off by default. To enable, install
`requirements-optional.txt` in the Dockerfile and set `ENABLE_VOICE=true` / `ENABLE_OCR=true`.

Once running, the API base URL is `https://<your-username>-<space-name>.hf.space`.
