"""Pipeline configuration."""
import os


def _load_dotenv():
    """Minimal .env loader: project root or cwd. First '=' splits key/value;
    the rest of the line is the value verbatim (libpq keyword strings contain '=')."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", ".env"), os.path.join(os.getcwd(), ".env")):
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line or line.lstrip().startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val)
            break


_load_dotenv()

# --- Models (from the claude-api skill, cached 2026-11-03) ---
# Extraction is bulk/classification -> cheaper Sonnet (or Haiku for cost).
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "claude-sonnet-5-5")
# Resolution needs judgment + web retrieval. Sonnet 5.5 for cost (~half of Opus 5.5)
# and it supports web_search_20260209. (Opus 4.8 would cost MORE than Opus 5.5 and is
# older — not a cost lever.) Consider escalating only hard/ambiguous claims to
# claude-opus-5-5; measure resolution accuracy vs known ground_truth before trusting it.
RESOLVE_MODEL = os.environ.get("RESOLVE_MODEL", "claude-sonnet-5-5")

# Web search server-tool variant supported on Opus 5.5 / Sonnet 5.5 (dynamic filtering).
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}

# --- Pricing (approximate; VERIFY against the claude-api skill / console) ---
# Sonnet 5.5 per-million token prices and web_search server-tool price.
SONNET_IN_PER_M = float(os.environ.get("SONNET_IN_PER_M", "2.0"))    # USD / 1M input tokens
SONNET_OUT_PER_M = float(os.environ.get("SONNET_OUT_PER_M", "10.0")) # USD / 1M output tokens
WEB_SEARCH_PER_1K = float(os.environ.get("WEB_SEARCH_PER_1K", "10.0"))  # USD / 1k searches

# --- Scoring (must match scripts/test_a.py) ---
PARENT_STRENGTH = 10.0
CI = (0.05, 0.95)   # 90% credible interval
SEP = ">"           # slug separator used in claims / hidden_quality keys

# --- Taxonomy canonicalization ---
# Cosine-similarity threshold above which a proposed node maps to an existing one.
NODE_MATCH_THRESHOLD = 0.88
# Embedder: NO Claude embeddings endpoint — pick Voyage/OpenAI/etc. and set dim
# to match topic_nodes.embedding in db/001_schema.sql (placeholder 1536).
EMBED_MODEL = os.environ.get("EMBED_MODEL", "")  # TODO: set

# --- Supabase ---
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL", "")  # postgres connection string

# --- X / Twitter API (ingestion for live authors) ---
# Pay-per-use access (default for new devs since Feb 2026) includes full-archive
# search per X's own docs. Need a Bearer token with full-archive access enabled.
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
X_API_BASE = os.environ.get("X_API_BASE", "https://api.x.com/2")
# Reported pay-per-use read price; VERIFY in the X developer console before trusting cost estimates.
X_READ_PRICE = float(os.environ.get("X_READ_PRICE", "0.005"))  # USD per post read
# Rough fraction of pulled posts that are falsifiable claims (for cost estimates only).
FALSIFIABLE_YIELD = float(os.environ.get("FALSIFIABLE_YIELD", "0.10"))

TODAY = os.environ.get("LEDGER_AS_OF", "")  # 'YYYY-MM-DD'; default = date.today() in code
