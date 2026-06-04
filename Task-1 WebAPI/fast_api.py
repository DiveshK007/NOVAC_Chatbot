from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pymongo import MongoClient, ReturnDocument
import os
import re
import ast
import operator as _op
import string
from decimal import Decimal, InvalidOperation
import time
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from nltk.tokenize import word_tokenize
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from pypdf import PdfReader
from pydantic import BaseModel, EmailStr
from paddleocr import PaddleOCR
import whisper
from mistralai.client import MistralClient
from dotenv import load_dotenv
from groq import Groq
import json
import requests
from fastapi.responses import StreamingResponse
from io import BytesIO
# Load environment variables
load_dotenv()
app = FastAPI()
# Allow React frontend connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
model = SentenceTransformer('all-MiniLM-L6-v2')
ocr = PaddleOCR(lang='en')
whisper_model = whisper.load_model("base")
client = MongoClient("mongodb://localhost:27017/")
db = client["novac_db"]
# MongoDB collection and Mistral client
collection = db["chunks"]
mistral_api_key = os.getenv("MISTRAL_API_KEY")
mistral_client = MistralClient(api_key=mistral_api_key)
users_collection = db["users"]
counters_collection = db["counters"]

groq_api_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=groq_api_key)

# ElevenLabs Configuration
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")

# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def is_bcrypt_hash(value) -> bool:
    return isinstance(value, str) and value.startswith(("$2a$", "$2b$", "$2y$"))

# ---------------------------------------------------------------------------
# JWT authentication
# ---------------------------------------------------------------------------
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-insecure-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 12

security = HTTPBearer()

def create_access_token(email: str, role: str) -> str:
    payload = {
        "sub": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"email": payload.get("sub"), "role": payload.get("role", "user")}

def require_admin(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# ---------------------------------------------------------------------------
# Atomic, collision-free chunk id generator
# ---------------------------------------------------------------------------
def next_chunk_id() -> int:
    doc = counters_collection.find_one_and_update(
        {"_id": "chunk_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])

# Ensure both admin and user exist, with hashed passwords.
# Existing plaintext passwords are migrated to bcrypt in place.
def ensure_user(email: str, password: str, role: str):
    existing = users_collection.find_one({"email": email})
    if not existing:
        users_collection.insert_one(
            {"email": email, "password": hash_password(password), "role": role}
        )
        return
    updates = {}
    if "role" not in existing:
        updates["role"] = role
    if not is_bcrypt_hash(existing.get("password")):
        # Re-hash whatever plaintext value is currently stored so the same
        # password keeps working after migration.
        updates["password"] = hash_password(existing.get("password") or password)
    if updates:
        users_collection.update_one({"email": email}, {"$set": updates})

ensure_user("admin@novac.com", "admin", "admin")
ensure_user("user@novac.com", "user", "user")

# Conversational retrieval memory, keyed per authenticated user.
# Each entry: {"results": [...], "index": int, "query": str}
conversation_state = {}

def reset_conversation_memory():
    """Clear all conversational memory (corpus changed)."""
    conversation_state.clear()

# ---------------------------------------------------------------------------
# Multilingual helpers
# ---------------------------------------------------------------------------
# Appended to prompts so the model tags which language it answered in.
LANG_TAG = (
    "\n\nAt the very end of your reply, on a new line, append exactly: "
    "[LANG: <name of the language/script the user wrote in, e.g. Tamil, "
    "Tanglish, Hindi, Hinglish, English>]. Do not mention this tag anywhere else."
)

_LANG_RE = re.compile(r"\[LANG:\s*([^\]\n]+)\]", re.IGNORECASE)

MISTRAL_FALLBACK = (
    "Sorry, I'm having trouble reaching the AI service right now. "
    "Please try again in a moment."
)

# Chat models per provider. Swap these to upgrade (e.g. "mistral-large-latest").
MISTRAL_CHAT_MODEL = "mistral-small-latest"
GROQ_CHAT_MODEL = "llama-3.3-70b-versatile"
# xAI Grok (separate provider, OpenAI-compatible REST API). Override the model
# with GROK_MODEL in .env (e.g. grok-4, grok-3-mini) — verify IDs at x.ai docs.
GROK_CHAT_MODEL = os.getenv("GROK_MODEL", "grok-3")
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_BASE_URL = "https://api.x.ai/v1"
SUPPORTED_PROVIDERS = {"mistral", "groq", "grok"}

# The calc protocol (tool-based math) is triggered UNRELIABLY by some chat models —
# mistral-small in particular tends to list the operands instead of emitting a calc
# block. So the calc DECISION is routed to this provider (the one verified to trigger it
# consistently) whenever the user's selected provider fails to; the user's provider still
# writes the final prose. Pure model routing — nothing document-specific is hardcoded.
MATH_PROVIDER = "groq"

# Case-study mode: when a question names a person/subject that is NOT in the document,
# treat the name as a placeholder for the document's illustrative example and answer using
# the document's own figures — but ALWAYS state up front that the name isn't in the
# document and name the example the figures come from. Numbers still must appear verbatim
# in the context (the provenance guard is untouched), so this never fabricates values; it
# only relabels the document's example. Set False for strict grounding (unknown name ->
# "not found"). Generic: no document- or name-specific logic.
CASE_STUDY_MODE = True

def mistral_reply(prompt: str, temperature: float = 0.1, retries: int = 2) -> str:
    """Call Mistral with a couple of retries; on persistent failure return a
    graceful message instead of raising (so the chat never hard-fails)."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = mistral_client.chat(
                model=MISTRAL_CHAT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    print("Mistral call failed after retries:", last_error)
    return MISTRAL_FALLBACK

def groq_reply(prompt: str, temperature: float = 0.1, retries: int = 2) -> str:
    """Call Groq (Llama 3.3) with retries; mirrors mistral_reply's graceful
    fallback so a provider hiccup never hard-fails the chat."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    print("Groq call failed after retries:", last_error)
    return MISTRAL_FALLBACK

def grok_reply(prompt: str, temperature: float = 0.1, retries: int = 2) -> str:
    """Call xAI Grok via its OpenAI-compatible REST endpoint (no extra SDK needed).
    Falls back gracefully like the other providers."""
    if not XAI_API_KEY:
        print("Grok call skipped: XAI_API_KEY is not set in .env")
        return MISTRAL_FALLBACK
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROK_CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{XAI_BASE_URL}/chat/completions",
                json=payload, headers=headers, timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    print("Grok call failed after retries:", last_error)
    return MISTRAL_FALLBACK

def llm_reply(prompt: str, provider: str = "mistral", temperature: float = 0.1,
              retries: int = 2) -> str:
    """Unified chat dispatch so the same prompt can run through any provider.
    Unknown providers fall back to Mistral."""
    if provider == "groq":
        return groq_reply(prompt, temperature=temperature, retries=retries)
    if provider == "grok":
        return grok_reply(prompt, temperature=temperature, retries=retries)
    return mistral_reply(prompt, temperature=temperature, retries=retries)

def extract_json_array(text: str) -> str:
    """Pull a JSON array out of an LLM reply that may be wrapped in markdown
    code fences or surrounded by prose (Mistral/Grok tend to do this, Groq
    usually doesn't). Returns the original text if no array delimiters are
    found, so json.loads will fail and the caller can fall back to raw chunks."""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    start, end = t.find("["), t.rfind("]")
    if start != -1 and end != -1 and end > start:
        return t[start:end + 1]
    return t

# ---------------------------------------------------------------------------
# Tool-based computation: safe arithmetic evaluator
# ---------------------------------------------------------------------------
# The LLM stays a reasoner/extractor — it pulls the relevant numbers out of the
# retrieved context and decides what operation is needed. Python does the actual
# arithmetic, so a stated figure is correct by construction rather than a
# predicted (and sometimes silently wrong) digit. Only numeric literals and the
# operators + - * / ** % ( ) are permitted — no names, calls, attributes, or
# subscripts — so an expression can never execute arbitrary code.
_ALLOWED_BINOPS = {
    ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
    ast.Div: _op.truediv, ast.Pow: _op.pow, ast.Mod: _op.mod,
}
_ALLOWED_UNARYOPS = {ast.UAdd: _op.pos, ast.USub: _op.neg}

class CalcError(Exception):
    """Raised when an expression contains anything beyond safe arithmetic."""

def _eval_node(node):
    if isinstance(node, ast.Constant):
        # bool is a subclass of int — reject it so "True * 5" can't sneak through.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("only numeric literals are allowed")
        # Compute in Decimal, not float: money math must be exact. str() of the
        # literal round-trips the value the user typed (e.g. 0.4913 -> "0.4913").
        return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise CalcError("exponent too large")
        try:
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        except ZeroDivisionError:
            raise CalcError("division by zero")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise CalcError("unsupported expression")

def safe_eval_arithmetic(expr: str):
    """Evaluate a pure-arithmetic expression safely. Raises CalcError on anything
    that isn't numbers and + - * / ** % ( )."""
    expr = (expr or "").strip()
    if not expr or len(expr) > 200:
        raise CalcError("empty or oversized expression")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise CalcError("could not parse expression")
    return _eval_node(tree.body)

def format_number(value):
    """Render a computed number cleanly: rounded to 4 dp with trailing zeros (and a
    bare decimal point) stripped, so 6000.0000 -> "6000" and 8239.1010 -> "8239.101"."""
    if isinstance(value, Decimal):
        try:
            value = value.quantize(Decimal("0.0001"))
        except InvalidOperation:
            pass  # result too large to quantize — fall back to its full form
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{round(value, 4):f}".rstrip("0").rstrip(".")
    return str(value)

_CALC_BLOCK_RE = re.compile(r"```calc\s*(.+?)```", re.DOTALL | re.IGNORECASE)

def extract_calc_requests(text: str):
    """Pull the model's calc protocol block (a ```calc fenced JSON array) out of a
    Pass-1 reply. Returns a list of {"expr", "label"} dicts, or [] if the reply is a
    normal answer with no calc block (or the block is unparseable)."""
    if not text:
        return []
    m = _CALC_BLOCK_RE.search(text)
    if m:
        payload = m.group(1)
    elif '"expr"' in text and text.strip().startswith("["):
        # Some models drop the fence and emit just the JSON array — accept that too.
        payload = text.strip()
    else:
        return []
    try:
        items = json.loads(extract_json_array(payload))
    except Exception:
        return []
    requests = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and str(it.get("expr", "")).strip():
            requests.append({
                "expr": str(it["expr"]).strip(),
                "label": str(it.get("label", "")).strip(),
            })
    return requests

# ---------------------------------------------------------------------------
# Operand-provenance validation (the "no number-twisting" guard)
# ---------------------------------------------------------------------------
# Correct arithmetic on the WRONG numbers is still wrong — and silently so. Before
# we compute anything, every DATA number in the expression must be proven to appear
# verbatim in the retrieved context. If even one doesn't, the calculation is refused
# (reported as not-found) rather than returning a confident wrong figure. This is the
# difference between "the model promised it only used context numbers" and "the system
# verified it." Matching is comma/currency-insensitive so the Indian grouping in the
# document (₹1,50,000) lines up with the bare operand (150000) the model writes.
#
# A short, document-AGNOSTIC whitelist of unit/period conversion constants is allowed
# without grounding (e.g. 100 for a percentage, 12 months, 4 quarters, 365 days). These
# are arithmetic structure, not document data, so permitting them keeps "X% of Y" and
# per-period math working without ever hardcoding anything about a specific document.
_STRUCTURAL_CONSTANTS = {"0", "1", "2", "3", "4", "6", "12", "24", "52", "100", "360", "365"}
_OPERAND_RE = re.compile(r"\d+(?:\.\d+)?")          # numbers as written inside an expr
_CONTEXT_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")  # numbers in prose (may carry commas)

def _num_key(token: str):
    """Canonical comparison key for a numeric string: drop digit-grouping commas and
    insignificant trailing zeros so 16,770 / 16770 / 16770.00 all collapse to '16770'."""
    try:
        d = Decimal(token.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"

def extract_context_numbers(context: str):
    """Set of canonical numeric keys for every number that appears in the context."""
    keys = set()
    for tok in _CONTEXT_NUM_RE.findall(context or ""):
        key = _num_key(tok)
        if key is not None:
            keys.add(key)
    return keys

def ungrounded_operands(expr: str, context_keys):
    """Operands in expr that are neither a structural constant nor present verbatim in
    the context. A non-empty result means the expression must NOT be computed."""
    missing = []
    for tok in _OPERAND_RE.findall(expr):
        key = _num_key(tok)
        if key is None or key in _STRUCTURAL_CONSTANTS or key in context_keys:
            continue
        if tok not in missing:
            missing.append(tok)
    return missing

def run_calculations(requests, context):
    """Validate provenance, then evaluate each calc request in Python. Returns a list of
    result dicts, each with the original label/expr plus either a computed 'result'
    string or an 'error' (ungrounded operand, or unsafe/invalid expression)."""
    context_keys = extract_context_numbers(context)
    results = []
    for req in requests:
        entry = {"label": req["label"], "expr": req["expr"]}
        missing = ungrounded_operands(req["expr"], context_keys)
        if missing:
            entry["error"] = (
                "operand(s) not found verbatim in the context: " + ", ".join(missing)
            )
            results.append(entry)
            continue
        try:
            entry["result"] = format_number(safe_eval_arithmetic(req["expr"]))
        except CalcError as e:
            entry["error"] = str(e)
        results.append(entry)
    return results

def build_sources(chunks):
    """Shape chunks into source-card payloads for the frontend."""
    return [
        {
            "document_name": c["document_name"],
            "chunk_title": c["chunk_title"],
            "similarity": round(c["similarity"], 3),
        }
        for c in chunks
    ]

def extract_language(text: str):
    """Strip the [LANG: x] tag from an LLM reply.
    Returns (clean_text, language_or_None)."""
    if not text:
        return text, None
    matches = _LANG_RE.findall(text)
    language = matches[-1].strip() if matches else None
    clean = _LANG_RE.sub("", text).strip()
    return clean, language

LANG_MATCH_RULE = (
    "Reply in the EXACT same language and script the user used. "
    "If the user wrote in plain English, reply ONLY in plain English. "
    "If Hindi, reply in Hindi. If Tamil, reply in Tamil. "
    "If romanized Tanglish/Hinglish, reply in that same romanized form. "
    "Never switch to a different language than the user's."
)

def in_language_message(user_text: str, instruction: str):
    """Generate a short message in the same language/script the user used."""
    prompt = f"""The user wrote: "{user_text}"

{instruction}
{LANG_MATCH_RULE} Keep it to one short sentence.{LANG_TAG}"""
    return extract_language(mistral_reply(prompt, temperature=0.0))

# Small-talk that should be answered conversationally (in-language), not via RAG.
GREETING_WORDS = {
    "hi", "hello", "hey", "hii", "heyy", "good morning", "good evening",
    "good afternoon", "vanakkam", "namaste", "namaskaram",
}
CASUAL_WORDS = {
    "good", "fine", "nice", "cool", "okay", "ok", "thanks", "thank you",
    "ok da", "sari", "sari da", "nandri", "dhanyavaad", "shukriya",
}

# Retrieval / follow-up tuning
TOP_K = 6                       # chunks fed to the LLM per answer (after reranking)
CANDIDATE_K = 20                # semantic shortlist that gets hybrid-reranked
SEMANTIC_WEIGHT = 0.7           # hybrid score = 0.7*semantic + 0.3*keyword overlap
KEYWORD_WEIGHT = 0.3
UNKNOWN_DOC = "Unknown Document"  # sentinel for chunks with no document_name

# Tiny stopword set so keyword overlap focuses on meaningful tokens (proper nouns,
# numbers, units, dates, key terms) rather than glue words.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "in",
    "on", "for", "and", "or", "if", "what", "which", "who", "whom", "will", "would",
    "can", "could", "do", "does", "did", "how", "much", "many", "at", "by", "with",
    "as", "that", "this", "it", "his", "her", "their", "you", "your", "i", "me",
    "give", "tell", "show", "list", "all", "any", "from", "during", "after", "before",
}

def _tokenize(text: str):
    """Lowercase word tokens with surrounding punctuation stripped."""
    return [w.strip(string.punctuation) for w in text.lower().split()]

def keyword_score(query: str, text: str) -> float:
    """Fraction of meaningful query tokens that appear in the chunk text.
    Rescues exact entity/number matches (proper nouns, percentages, ordinals)
    that dense embeddings like MiniLM often rank too low."""
    q_terms = {w for w in _tokenize(query) if w and len(w) > 1 and w not in _STOPWORDS}
    if not q_terms:
        return 0.0
    haystack = text.lower()
    hits = sum(1 for w in q_terms if w in haystack)
    return hits / len(q_terms)

# ---------------------------------------------------------------------------
# Per-entity retrieval (multi-entity / comparison queries)
# ---------------------------------------------------------------------------
# A single blended embedding for a multi-entity question (e.g. "Compare X's and Y's
# figures") tends to retrieve chunks about neither subject specifically — or only one
# of them. When the question names subjects, we run an extra keyword-grounded pass per
# name so EVERY named subject's chunks are guaranteed into the context, then union that
# with the normal hybrid top-K.
PER_ENTITY_K = 2        # chunks force-included per named entity
MAX_CONTEXT = 9         # hard cap on chunks fed to the LLM after the union

# Generic English query scaffolding (question words, command verbs, auxiliaries,
# function words). A capitalized query token that is NOT one of these is treated as a
# candidate proper noun. Deliberately document-agnostic — no domain terms — so the
# same logic works for any uploaded document.
_QUERY_NOISE = {
    # question words
    "which", "what", "who", "whom", "whose", "where", "when", "why", "how",
    # command verbs
    "compare", "list", "show", "tell", "give", "explain", "find", "describe",
    "summarize", "summarise", "define", "assume", "calculate",
    # auxiliaries / common verbs
    "is", "are", "was", "were", "be", "do", "does", "did", "will", "would",
    "can", "could", "should", "has", "have", "had",
    # articles / conjunctions / prepositions
    "the", "a", "an", "and", "or", "if", "of", "to", "in", "on", "at", "for",
    "with", "from", "by", "about", "during", "after", "before", "between",
}

def extract_query_entities(query: str):
    """Proper-noun-ish tokens from the query (capitalized words that aren't generic
    scaffolding), with any possessive ('s) stripped. These drive per-entity retrieval
    so a comparison query pulls chunks for every named subject, not just whichever one
    the blended embedding happened to rank highest."""
    entities, seen = [], set()
    for raw in re.findall(r"[A-Za-z][A-Za-z']*", query):
        token = re.sub(r"'s?$", "", raw)          # drop trailing possessive
        if len(token) < 3 or not token[0].isupper():
            continue
        if token.lower() in _QUERY_NOISE:
            continue
        key = token.lower()
        if key not in seen:
            seen.add(key)
            entities.append(token)
    return entities

def _mentions_entity(text: str, entity: str) -> bool:
    return re.search(rf"\b{re.escape(entity)}\b", text, re.IGNORECASE) is not None

def merge_entity_chunks(top_chunks, similarities, entities):
    """Union the hybrid top-K with the best chunks that actually mention each
    named entity, deduped by chunk text and capped at MAX_CONTEXT. Forced
    per-entity chunks go first so they can never be squeezed out of context."""
    if not entities:
        return top_chunks
    merged, seen = [], set()

    def add(chunk):
        key = chunk["chunk"]
        if key not in seen:
            seen.add(key)
            merged.append(chunk)

    for ent in entities:
        hits = [c for c in similarities
                if _mentions_entity(f"{c['chunk_title']} {c['chunk']}", ent)]
        for c in hits[:PER_ENTITY_K]:
            add(c)
    for c in top_chunks:
        add(c)
    return merged[:MAX_CONTEXT]

# ---------------------------------------------------------------------------
# Enumeration / literal-filter queries ("list every item that has both X and Y")
# ---------------------------------------------------------------------------
# Semantic + hybrid retrieval ranks by *similarity*, so an exhaustive "find every
# chunk that literally contains X and Y" query misses matches that simply aren't in
# the top-K shortlist. For these we scan the WHOLE corpus for the literal tokens the
# user pinned (percentages, currency amounts) and force in every chunk that contains
# ALL of them.
_LITERAL_PATTERNS = [
    r"\d+(?:\.\d+)?%",                  # percentages: 12%, 30.5%
    r"[₹$€£¥]\s?[\d,]+(?:\.\d+)?",      # currency amounts: $1,200.50, €999
]

def extract_literal_constraints(query: str):
    """Pin the exact tokens an enumeration query filters on. Deduped, order-preserved."""
    found, seen = [], set()
    for pat in _LITERAL_PATTERNS:
        for m in re.findall(pat, query):
            if m not in seen:
                seen.add(m)
                found.append(m)
    return found

def is_enumeration_query(query: str) -> bool:
    """True for exhaustive 'find every match' questions (list all / which ... contain /
    with both ...) — as opposed to a single-fact lookup or a superlative."""
    q = f" {query.lower()} "
    if any(w in q for w in (" list ", " all ", " every ", " each ")):
        return True
    if ("contain" in q or " with " in q or " have " in q) and (" both " in q or " all " in q):
        return True
    return False

def literal_filter_chunks(query: str, similarities):
    """Every corpus chunk that literally contains ALL the query's pinned tokens,
    ranked by semantic similarity. Empty if the query isn't an enumeration or pins
    no literal tokens, so normal queries are unaffected."""
    if not is_enumeration_query(query):
        return []
    constraints = extract_literal_constraints(query)
    if not constraints:
        return []
    matches = [
        c for c in similarities
        if all(tok in f"{c['chunk_title']} {c['chunk']}" for tok in constraints)
    ]
    return matches[:MAX_CONTEXT]

# ---------------------------------------------------------------------------
# Grounded answer generation (with optional Python-computed math pass)
# ---------------------------------------------------------------------------
# Shared rule blocks so Pass 1 (which may request a calculation) and Pass 2 (which
# states the computed result) stay perfectly consistent in their grounding rules.
_STRICT_RULES = """STRICT RULES:
1. Answer ONLY from the retrieved context above. Never use outside knowledge.
2. Every amount, percentage, date, name and number you state must appear VERBATIM in the
   context. Preserve it EXACTLY as written. Never invent a value.
3. ATTRIBUTION: Each value in the context belongs to the specific entity (person, item,
   product, period, etc.) it is written next to. Always pair a value with that exact
   entity. NEVER assign one entity's figure to a different one. If the question names an
   entity, use only the value(s) written for THAT entity.
4. PARTIAL ANSWERS ARE REQUIRED. If the question names one or more entities (people,
   items, products, periods, etc.), handle EACH one independently:
   - If an entity IS present in the context, answer using its own values.
   - If an entity is NOT present, write one short line saying it is not found, then
     CONTINUE and answer for every entity that IS present.
   Do NOT return the rule-9 "not found" sentence as long as at least ONE named entity is
   present — this holds even for a "compare side by side" question where another named
   entity is missing. Use rule 9 ONLY when NONE of the named entities appear in the context.
   - Extra unrelated figures for a present entity (a different metric than the one asked
     about) are not a reason to refuse; simply ignore the ones that don't match the question.
   Illustrative example (fictional, unrelated to any uploaded document) — question
   "Compare Alice's and Bob's score", context contains "Bob's score: 42" but no Alice:
     "Alice is not found in the document.
      Bob — score: 42"
5. COMPARING & SELECTING IS ALLOWED. You MAY compare, rank, or pick among values that
   already appear in the context — e.g. "which one is highest", "compare X and Y side by
   side", or listing items that meet a stated condition. Choosing the largest/smallest of
   values already written in the context is NOT inventing a number; it is selecting one
   that is present. When ranking, compare the ACTUAL numeric magnitude of each value
   (read digit-grouping/commas and units carefully so a larger number is not mistaken for
   a smaller one) and pick the true highest/lowest. Show the values you compared.
6. ARITHMETIC IS DONE BY TOOL, NEVER IN YOUR HEAD. If the question needs a sum,
   difference, product, quotient, percentage-of, or total-across-periods, you MUST derive
   it via the CALCULATION PROTOCOL below — emit a calc block; do NOT just list the
   operands and stop, and do NOT write the arithmetic out as text. Use ONLY numbers that
   appear VERBATIM in the context as operands; never compute in your head. You still must NOT
   introduce a new rate, term, premium, or assumption that is not written in the context
   (e.g. a hypothetical figure the user invents); a value that depends on such an unknown
   is not derivable and falls under rule 9.
7. If several variants of a value apply (e.g. different rates or scenarios), show each one
   clearly and separately.
8. Do NOT add advice, opinions, or extra explanations unless the user explicitly asks.
9. If the answer simply is not present in the context (and is not derivable by arithmetic
   from numbers that are, and no NAME HANDLING note above tells you otherwise), reply with
   exactly this sentence (translated into the user's language): "Exact information not found
   in the retrieved document sections." """

_CALC_PROTOCOL = """CALCULATION PROTOCOL (this OVERRIDES the answer format below when math is needed):
- TRIGGER — If the question asks for a value that is not written as-is but must be DERIVED
  by arithmetic from numbers that ARE in the context, you MUST use this protocol. This
  includes any difference, sum, total, "combined", "how much in total", product, ratio,
  "X% of Y", or converting a per-year figure to per-month/quarter, etc.
- In that case you MUST NOT write a normal answer, MUST NOT just list the operand values,
  and MUST NOT write the arithmetic as prose (e.g. never output "17,62,189 - 13,65,300").
  Instead reply with ONLY a fenced code block labelled calc containing a JSON array —
  nothing else, no prose, no [LANG] tag:
```calc
[{"expr": "16770 * 0.4913", "label": "annual premium payable"}]
```
- "expr" may contain ONLY numbers taken verbatim from the context and the operators
  + - * / ** ( ). Express a percentage as a division, e.g. 40.13% of 16770 -> "16770 * 40.13 / 100".
- EVERY data number in "expr" must appear VERBATIM in the context (commas and currency
  symbols are ignored when matching). Only basic conversion constants — 100 for a
  percentage, 12 for months, 4 for quarters, 365 for days, and 1 — may be introduced.
  The system VERIFIES this and will refuse to compute if any other number is not in the
  context, so never fabricate or guess a figure.
- Do NOT pre-combine numbers: write "1 + 5 / 100" not "1.05", and "16770 * 40.13 / 100"
  not "16770 * 0.4013". Each data figure must be one that is literally written in the context.
- Include multiple objects in the array if several values must be computed.
- If NO arithmetic is needed, ignore this protocol and answer normally per the rules."""

_ANSWER_FORMAT = """ANSWER FORMAT:
- For a single fact: Line 1 is the direct answer; then at most one short supporting line
  drawn straight from the context.
- For a comparison/ranking: give the direct answer first (the winner, or "X vs Y"), then
  list each entity with its value on its own line, each attributed by name.
- Keep it concise and factual; never restate the question.

LANGUAGE:
- Answer in the EXACT same language and script as the user's question
  (Hindi->Hindi, Tamil->Tamil, Tanglish->Tanglish, Hinglish->Hinglish, English->English)."""

def build_answer_prompt(query, combined_context, entity_note):
    """Pass-1 prompt: the model either answers directly or, if arithmetic on context
    numbers is needed, emits a calc protocol block instead of computing it itself."""
    return f"""
You are NOVAC AI, a STRICT document-grounded extraction assistant. Your only job is to
answer using the retrieved context below, for whatever document the user uploaded.

User question:
{query}
{entity_note}

Retrieved document context:
{combined_context}

{_STRICT_RULES}

{_CALC_PROTOCOL}

{_ANSWER_FORMAT}
{LANG_TAG}

Answer:
"""

def build_calc_answer_prompt(query, combined_context, entity_note, computed_lines):
    """Pass-2 prompt: the math has already been done in Python. The model writes the
    final grounded answer stating the exact computed value(s) — it must not recompute."""
    return f"""
You are NOVAC AI, a STRICT document-grounded extraction assistant.

User question:
{query}
{entity_note}

Retrieved document context:
{combined_context}

COMPUTED VALUES (calculated in Python from numbers in the context — these are EXACT and
authoritative; state them verbatim and do NOT recompute or alter them):
{computed_lines}

Write the final answer using the context and these computed values:
- State the relevant computed value(s) as the answer.
- If a computed value is shown as "could not be computed", treat that figure as not found.
- Never introduce a number that is neither in the context nor in the computed values.
- Do NOT emit another calc block.

{_ANSWER_FORMAT}
{LANG_TAG}

Answer:
"""

# Generic English math-intent signals — document-agnostic, in the same spirit as the
# existing greeting/enumeration heuristics. Used only to decide whether it's worth a
# fallback call to the reliable math provider; a miss simply leaves behaviour unchanged,
# so this never gates correctness.
_MATH_INTENT_RE = re.compile(
    r"\b(difference|differ|how much (more|less|higher|lower)|more than|less than|"
    r"total|sum|combined|altogether|adds? up|in total|overall|"
    r"multiply|multiplied|times|product|divided?|ratio|"
    r"average|mean|percent|percentage of|per annum|"
    r"over \d+ (year|month|quarter|week|day)s?)\b",
    re.IGNORECASE,
)

def looks_computational(query: str) -> bool:
    """Best-effort: does the question read like it needs arithmetic on context numbers?"""
    return bool(_MATH_INTENT_RE.search(query or ""))

def generate_grounded_answer(query, combined_context, entity_note, provider):
    """Generate the final answer, with an optional Python-computed math pass.
    Pass 1: the model answers directly OR emits a calc protocol block. If it asked for
    arithmetic, evaluate it safely in Python and run Pass 2 so the final answer states
    exact, machine-computed figures instead of hallucinated digits. Returns
    (clean_text, detected_language) — the same shape as extract_language()."""
    raw = llm_reply(build_answer_prompt(query, combined_context, entity_note), provider=provider)

    calc_requests = extract_calc_requests(raw)

    # Reliability fallback: some chat models (mistral-small) won't emit a calc block even
    # when arithmetic is needed — they list the operands and stop. If the selected
    # provider didn't trigger it and the question looks computational, ask the reliable
    # math provider to make the calc DECISION. The user's provider still writes the prose
    # in Pass 2 below, so their provider choice is preserved for everything user-facing.
    if (not calc_requests and provider != MATH_PROVIDER
            and looks_computational(query)):
        fallback = llm_reply(
            build_answer_prompt(query, combined_context, entity_note),
            provider=MATH_PROVIDER,
        )
        calc_requests = extract_calc_requests(fallback)

    if not calc_requests:
        return extract_language(raw)

    results = run_calculations(calc_requests, combined_context)
    computed_lines = "\n".join(
        (f"- {r['label'] or r['expr']}: {r['expr']} = {r['result']}" if "result" in r
         else f"- {r['label'] or r['expr']}: {r['expr']} could not be computed ({r['error']})")
        for r in results
    )
    final = llm_reply(
        build_calc_answer_prompt(query, combined_context, entity_note, computed_lines),
        provider=provider,
    )
    return extract_language(final)

# Authentication models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    
class DeleteDocsRequest(BaseModel):
    document_names: list[str]

@app.get("/")
async def home():
    return {
        "message": "FastAPI Backend Running"
    }


@app.post("/login")
async def login(data: LoginRequest):

    # For manual convenience if user types standard login
    email = data.email
    password = data.password
    
    # Check manual overrides for easy task
    if password == "admin123" or email == "admin":
         email = "admin@novac.com"
         password = "admin"
    if password == "user123" or email == "user":
         email = "user@novac.com"
         password = "user"

    user = users_collection.find_one({"email": email})

    if not user or not verify_password(password, user.get("password", "")):
        return {
            "success": False,
            "message": "Invalid email or password"
        }

    role = user.get("role", "user")

    return {
        "success": True,
        "message": "Login successful",
        "token": create_access_token(user["email"], role),
        "user": {
            "email": user["email"],
            "role": role
        }
    }

@app.delete("/chunks/documents")
async def delete_documents(req: DeleteDocsRequest, user: dict = Depends(require_admin)):
    docs_to_delete = req.document_names
    if not docs_to_delete:
         return {"success": False, "message": "No documents provided"}
         
    try:
         result = collection.delete_many({"document_name": {"$in": docs_to_delete}})
         reset_conversation_memory()
         return {
              "success": True,
              "message": f"Deleted {result.deleted_count} chunks from {len(docs_to_delete)} documents",
              "deleted_count": result.deleted_count
         }
    except Exception as e:
         return {"success": False, "message": str(e)}

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    document_name: str = Form(...),
    chunk_model: str = Form("mistral", alias="model"),
    user: dict = Depends(require_admin)
):
    # Provider used for semantic chunking (chosen in the UI, same dropdown as chat).
    # NOTE: param is named chunk_model, not `model`, to avoid shadowing the global
    # SentenceTransformer `model` used for embeddings below.
    provider = chunk_model if chunk_model in SUPPORTED_PROVIDERS else "mistral"
    os.makedirs("uploads", exist_ok=True)
    filepath = os.path.join("uploads", file.filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    # Extract text from TXT
    if file.filename.endswith(".txt"):
        text = content.decode("utf-8")
    # Extract text from PDF
    elif file.filename.endswith(".pdf"):
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + " "
    # Extract text from Images
    elif (
        file.filename.endswith(".png") or
        file.filename.endswith(".jpg") or
        file.filename.endswith(".jpeg")
    ):
        result = ocr.ocr(filepath)
        extracted_lines = []
        if result:
            for page in result:
                if isinstance(page, dict) and "rec_texts" in page:
                    for detected_text in page["rec_texts"]:
                        if detected_text and len(detected_text.strip()) > 2:
                            extracted_lines.append(detected_text.strip())
                elif isinstance(page, list):
                    for line in page:
                        try:
                            detected_text = line[1][0]
                            if detected_text and len(detected_text.strip()) > 2:
                                extracted_lines.append(detected_text.strip())
                        except:
                            pass
        text = ". ".join(extracted_lines)
        text = text.replace("\n", " ")
        text = text.replace("|", " ")
        text = text.replace("_", " ")
        text = text.replace("-", " ")
        text = text.replace("..", ".")
        text = " ".join(text.split())
        if not any(p in text for p in [".", "!", "?"]):
            text += "."
    else:
        return {
            "message": "Only TXT, PDF and Image files supported currently"
        }
    text = text.replace("\n", " ")
    text = " ".join(text.split())

    # Paragraph-aware rough semantic sections
    paragraphs = text.split('. ')

    rough_sections = []
    current_section = ""

    for para in paragraphs:

        para = para.strip()

        if not para:
            continue

        para += ". "

        if len(current_section) + len(para) < 3500:
            current_section += para
        else:
            rough_sections.append(current_section.strip())
            current_section = para

    if current_section.strip():
        rough_sections.append(current_section.strip())

    chunks = []

    for section in rough_sections:

        chunk_prompt = f"""
        You are an advanced semantic chunking engine for Retrieval-Augmented Generation systems.

        Your job is to divide the provided document section into MULTIPLE meaningful retrieval chunks.

        VERY IMPORTANT:
        - Do NOT return only headings.
        - Do NOT summarize.
        - Do NOT compress the content.
        - Preserve the original information.
        - Every chunk must contain actual explanatory content.
        - Each chunk should focus on ONE major topic.
        - Split whenever the topic shifts significantly.
        - Avoid giant full-document chunks.
        - Avoid tiny title-only chunks.

        OUTPUT RULES:
        - Return ONLY valid JSON.
        - Return a JSON array of objects.
        - Every object must contain:
            - title
            - content
        - The title should be a short meaningful subheading.
        - The content should contain detailed contextual information.

        GOOD OUTPUT EXAMPLE:
        [
          {{
            "title": "AI in Healthcare",
            "content": "Artificial Intelligence is transforming modern healthcare through predictive diagnostics, automation, and personalized treatment planning..."
          }},
          {{
            "title": "RAG Architecture",
            "content": "Retrieval-Augmented Generation combines semantic retrieval with large language models to improve contextual accuracy and reduce hallucinations..."
          }}
        ]

        DOCUMENT SECTION:
        {section}
        """

        # Chunk with the user-selected provider. llm_reply returns a graceful
        # fallback STRING (never raises) if the provider is unavailable; that
        # string fails JSON parsing below and drops to the raw-section path,
        # so the upload always succeeds instead of 500-ing.
        raw_chunks = llm_reply(chunk_prompt, provider=provider, temperature=0.1)

        try:
            parsed_chunks = json.loads(extract_json_array(raw_chunks))

            for chunk in parsed_chunks:

                if not isinstance(chunk, dict):
                    continue

                title = chunk.get("title", "Untitled Chunk").strip()
                content = chunk.get("content", "").strip()

                # Remove malformed chunks
                if len(content) < 80:
                    continue

                if content.lower().startswith("utions"):
                    continue

                if len(content.split()) < 20:
                    continue

                chunks.append({
                    "title": title,
                    "content": content
                })

        except:
            chunks.append({
                "title": "Document Section",
                "content": section
            })

    print("FINAL CHUNKS:", chunks)
    # Reset conversational memory for everyone (the corpus just changed)
    reset_conversation_memory()

    # Overwrite old chunks for same document name
    collection.delete_many({
        "document_name": document_name
    })

    saved_chunks = []
    for idx, chunk_data in enumerate(chunks):

        chunk_title = chunk_data["title"]
        chunk_content = chunk_data["content"]

        embedding = model.encode(chunk_content).tolist()

        document = {
            "chunk_id": next_chunk_id(),
            "document_name": document_name,
            "original_filename": file.filename,
            "chunk_title": chunk_title,
            "chunk": chunk_content,
            "embedding": embedding
        }
        collection.insert_one(document)
        saved_chunks.append({
            "chunk_id": document["chunk_id"],
            "document_name": document_name,
            "chunk_title": chunk_title,
            "chunk": chunk_content
        })
    return {
        "message": "File uploaded successfully",
        "chunks": saved_chunks
    }

# Semantic search endpoint
class SearchQuery(BaseModel):
    query: str
    model: str = "mistral"   # "mistral" or "groq" — chosen in the UI
# Chunk update model
class ChunkUpdate(BaseModel):
    chunk_id: int
    updated_chunk: str
@app.post("/search")
async def search(data: SearchQuery, user: dict = Depends(get_current_user)):

    # Which LLM provider answers this turn (selected in the UI).
    provider = data.model if data.model in SUPPORTED_PROVIDERS else "mistral"

    # Per-user conversational memory
    state = conversation_state.setdefault(
        user["email"], {"query": "", "fu_queue": [], "fu_index": 0}
    )

    user_query = data.query.lower().strip()

    # Small talk (greetings / casual acknowledgements) -> reply in-language, no RAG
    if user_query in GREETING_WORDS or user_query in CASUAL_WORDS:
        smalltalk_prompt = f"""The user sent this short message: "{data.query}"

This is small talk (a greeting or a casual acknowledgement), not a document question.
Reply with ONE short, warm, friendly sentence.
If it's a greeting, greet them back and invite them to ask about their uploaded documents.
If it's a thanks/acknowledgement, respond politely and offer further help.
{LANG_MATCH_RULE}{LANG_TAG}"""
        reply, language = extract_language(mistral_reply(smalltalk_prompt, temperature=0.0))
        return {"response": reply, "detected_language": language}

    # Continue previous conversation
    follow_up_words = [
        "more",
        "tell me more",
        "explain more",
        "continue",
        "next",
        "details"
    ]

    if (
        user_query in follow_up_words and
        state.get("query")
    ):
        # Next batch from the same-document relevance queue (built on the last fresh search).
        queue = state.get("fu_queue", [])
        fu_index = state.get("fu_index", 0)
        batch = queue[fu_index:fu_index + TOP_K]

        if not batch:
            reply, language = in_language_message(
                state["query"],
                "You have already shared all the relevant information from the uploaded "
                "documents about their question. Politely tell them there is nothing more to add.",
            )
            return {"response": reply, "detected_language": language}

        state["fu_index"] = fu_index + len(batch)

        follow_up_context = "\n\n".join(
            f"Title: {c['chunk_title']}\n\nContent:\n{c['chunk']}" for c in batch
        )

        prompt = f"""
        The user originally asked: {state["query"]}

        They have now asked for MORE details. Below is ADDITIONAL context that was NOT part
        of your previous answer:

        {follow_up_context}

        Provide NEW, additional details about their original question using ONLY this new context.
        Do NOT repeat points you already gave, and do NOT restate the question.
        If this context adds nothing useful, say you've already covered the available information.
        CRITICAL: Answer in the EXACT same language and script as the user's original question
        (Tamil->Tamil, Tanglish->Tanglish, Hindi->Hindi, Hinglish->Hinglish, English->English).{LANG_TAG}
        """

        reply, language = extract_language(llm_reply(prompt, provider=provider))

        return {
            "response": reply,
            "detected_language": language,
            "chunks_used": len(batch),
            "sources": build_sources(batch)
        }

    # Fresh semantic search
    query_embedding = model.encode(data.query)

    all_chunks = list(collection.find())

    similarities = []

    for chunk in all_chunks:

        similarity = cosine_similarity(
            [query_embedding],
            [chunk["embedding"]]
        )[0][0]

        similarities.append({
            "chunk": chunk["chunk"],
            "chunk_title": chunk.get("chunk_title", "Untitled Chunk"),
            "document_name": chunk.get("document_name") or UNKNOWN_DOC,
            "similarity": float(similarity)
        })

    similarities = sorted(
        similarities,
        key=lambda x: x["similarity"],
        reverse=True
    )

    # Save conversational memory (per user)
    state["query"] = data.query

    if len(similarities) == 0:

        reply, language = in_language_message(
            data.query,
            "There are no uploaded documents yet to answer from. "
            "Politely ask them to upload a document first.",
        )
        return {"response": reply, "detected_language": language}

    # Hybrid rerank: take a wide semantic shortlist, then re-score each candidate
    # as 0.7*normalized_semantic + 0.3*keyword_overlap. This lifts chunks that
    # mention the exact entity/number asked about (proper nouns, percentages,
    # ordinals) which dense MiniLM embeddings alone tend to under-rank.
    candidates = similarities[:CANDIDATE_K]
    sem_values = [c["similarity"] for c in candidates]
    sem_min, sem_max = min(sem_values), max(sem_values)
    sem_span = (sem_max - sem_min) or 1.0
    for c in candidates:
        sem_norm = (c["similarity"] - sem_min) / sem_span
        kw = keyword_score(data.query, f"{c['chunk_title']} {c['chunk']}")
        c["hybrid"] = SEMANTIC_WEIGHT * sem_norm + KEYWORD_WEIGHT * kw
    reranked = sorted(candidates, key=lambda x: x["hybrid"], reverse=True)

    top_chunks = reranked[:TOP_K]

    # Enumeration / literal-filter queries ("list every item that has both X and Y"):
    # a similarity ranking misses matches outside the top-K, so scan the whole corpus
    # for chunks that literally contain all the pinned tokens and put them first.
    # Empty (and a no-op) for ordinary lookups.
    literal_matches = literal_filter_chunks(data.query, similarities)
    if literal_matches:
        seen = {c["chunk"] for c in literal_matches}
        top_chunks = literal_matches + [c for c in top_chunks if c["chunk"] not in seen]
        top_chunks = top_chunks[:MAX_CONTEXT]

    # Multi-entity / comparison queries: force in the best chunks that actually
    # name each subject mentioned in the question, so the LLM sees EVERY subject's
    # figures (not just the one the blended embedding favored) and can attribute
    # and compare them correctly.
    entities = extract_query_entities(data.query)
    top_chunks = merge_entity_chunks(top_chunks, similarities, entities)

    # Build the follow-up ("more") queue: further chunks from the SAME document(s) this
    # answer drew from, still above the relevance floor — so "more" can't drift into
    # unrelated documents.
    # Scope follow-ups to the real document(s) this answer drew from. Orphan chunks with
    # no document_name (the UNKNOWN_DOC sentinel) are excluded, so "more" can't drift into
    # unrelated content. Within a scoped document, every remaining chunk is fair game —
    # no similarity floor, since the document scope is already the relevance guard.
    doc_scope = {
        c["document_name"] for c in top_chunks
        if c["document_name"] != UNKNOWN_DOC
    }
    shown_texts = {c["chunk"] for c in top_chunks}
    state["fu_queue"] = [
        c for c in reranked[TOP_K:]
        if c["document_name"] in doc_scope and c["chunk"] not in shown_texts
    ]
    state["fu_index"] = 0

    combined_context = "\n\n".join([
        f"""
Document: {chunk['document_name']}
Title: {chunk['chunk_title']}

Content:
{chunk['chunk']}
"""
        for chunk in top_chunks
    ])

    # Deterministic presence check: tell the model exactly which of the names it asked
    # about actually occur in the retrieved context and which don't. This removes the
    # ambiguity that makes a partial comparison (some subjects present, some absent)
    # flaky — without hardcoding anything document-specific. Only emitted in the mixed
    # case; all-present or all-absent queries get no note and behave as before.
    entity_note = ""
    if entities:
        present = [e for e in entities if _mentions_entity(combined_context, e)]
        missing = [e for e in entities if e not in present]
        if missing and CASE_STUDY_MODE:
            # Case-study mode: don't refuse on an unknown name. Treat it as a placeholder
            # for the document's illustration and answer with the document's own figures,
            # transparently. The numbers still come only from the context (provenance guard
            # unchanged), so this relabels the example rather than inventing anything.
            entity_note = (
                "\nNAME HANDLING (authoritative — follow exactly, override rule 9):\n"
                f"- These names are NOT written in the document: {', '.join(missing)}.\n"
                "- The document is a template/illustration, so treat each such name as a "
                "placeholder for the document's sample case. Answer the question for that "
                "name USING the document's illustrative figures from the context.\n"
                "- DETERMINISTIC CHOICE: if the context contains MORE THAN ONE example/"
                "illustration, ALWAYS use the one that appears FIRST in the context above "
                "(reading top to bottom). Never pick arbitrarily and never switch examples "
                "within one answer.\n"
                "- SCENARIO MATCH: if the question pins a specific scenario value (a policy "
                "year, term, age, premium amount, payout mode, etc.) that does NOT match the "
                "value used in the example you are drawing from, do NOT present the example's "
                "figures as the answer. Instead state plainly that the document only "
                "illustrates the example's value (e.g. 'only the 65th policy year, not the "
                "58th'), so a figure for the asked value is not available — UNLESS it can be "
                "derived from numbers in the context via the calculation protocol.\n"
                "- BEGIN your answer with one short line stating the name is not in the "
                "document and that the figures are from its example (name which example/"
                "entity in the document they belong to).\n"
                "- Use ONLY numbers that appear in the context; never invent new values. "
                "If the context has NO relevant figures to illustrate the answer, only then "
                "say the information is not found.\n"
            )
            if present:
                entity_note += (
                    f"- These names ARE in the document — answer them from their own "
                    f"values: {', '.join(present)}.\n"
                )
        elif present and missing:
            entity_note = (
                "\nFACT CHECK (use this — it is authoritative for these names):\n"
                f"- Present in the context: {', '.join(present)}\n"
                f"- NOT in the context (report each as not found): {', '.join(missing)}\n"
                "Answer for the present name(s) regardless of the missing one(s).\n"
            )

    reply, language = generate_grounded_answer(
        data.query, combined_context, entity_note, provider
    )

    return {
        "response": reply,
        "detected_language": language,
        "chunks_used": len(top_chunks),
        "sources": build_sources(top_chunks)
    }
# Retrieve all chunks endpoint
@app.get("/chunks")
async def get_chunks(user: dict = Depends(require_admin)):
    all_chunks = list(collection.find({}, {
        "_id": 0,
        "chunk_id": 1,
        "document_name": 1,
        "chunk_title": 1,
        "chunk": 1
    }))
    return {
        "chunks": all_chunks
    }

# Update a chunk endpoint
@app.post("/update-chunk")
async def update_chunk(data: ChunkUpdate, user: dict = Depends(require_admin)):

    # Generate new embedding for updated chunk
    updated_embedding = model.encode(
        data.updated_chunk
    ).tolist()

    collection.update_one(
        {
            "chunk_id": data.chunk_id
        },
        {
            "$set": {
                "chunk": data.updated_chunk,
                "embedding": updated_embedding
            }
        }
    )

    # Reset conversational retrieval memory (corpus changed)
    reset_conversation_memory()

    return {
        "message": "Chunk and embedding updated successfully"
    }

# Text to speech endpoint
@app.post("/tts")
async def text_to_speech(data: SearchQuery, user: dict = Depends(get_current_user)):

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "text": data.query,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }

    response = requests.post(
        url,
        json=payload,
        headers=headers
    )

    print("ElevenLabs Status:", response.status_code)

    if response.status_code != 200:
        print("ElevenLabs Error:", response.text)

        return {
            "error": response.text
        }

    audio_stream = BytesIO(response.content)

    return StreamingResponse(
        audio_stream,
        media_type="audio/mpeg"
    )

# Voice to text endpoint
@app.post("/voice")
async def voice_to_text(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    os.makedirs("uploads", exist_ok=True)
    filepath = os.path.join("uploads", file.filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    result = whisper_model.transcribe(filepath)
    return {
        "text": result["text"]
    }