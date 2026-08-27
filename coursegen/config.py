"""All tunables. No magic numbers anywhere else in the codebase."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).parent.parent
DATA_DIR: Path = BASE_DIR / "data"
OUTPUT_DIR: Path = BASE_DIR / "output"
QDRANT_PATH: str = str(DATA_DIR / "qdrant")
COURSE_MAP_PATH: Path = DATA_DIR / "course_map.json"

# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
MAX_CHUNK_TOKENS: int = 512
CHUNK_OVERLAP_PCT: float = 0.15
HEADING_STD_FACTOR: float = 1.5
HEADING_MAX_BLOCK_PCT: float = 0.30   # gate: <30% of blocks classified as headings
OCR_DPI: int = 150

# ---------------------------------------------------------------------------
# Security / parser limits (S3)
# ---------------------------------------------------------------------------
MAX_FILE_SIZE_BYTES: int = 100 * 1024 * 1024        # 100 MB
MAX_DECOMPRESSED_SIZE_BYTES: int = 500 * 1024 * 1024  # 500 MB
MAX_PAGES: int = 2_000
PARSE_TIMEOUT_SECONDS: int = 60

# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
EMBEDDING_MODEL: str = "BAAI/bge-m3"
DENSE_VECTOR_SIZE: int = 1024
QDRANT_COLLECTION_NAME: str = "coursegen"
EMBEDDING_BATCH_SIZE: int = 32

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
RETRIEVE_TOP_K: int = 10
RERANK_TOP_K: int = 5
SEND_TOP_K: int = 4                  # L9: retrieve 10, rerank to 5, send 4
RERANKER_THRESHOLD: float = 0.5
RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ---------------------------------------------------------------------------
# YAKE key-term extraction
# ---------------------------------------------------------------------------
YAKE_NGRAM_MAX: int = 3
YAKE_DEDUP_THRESHOLD: float = 0.9
YAKE_TOP_N: int = 10

# ---------------------------------------------------------------------------
# Exam generation
# ---------------------------------------------------------------------------
BATCH_SIZE: int = 6                  # item specs per LLM call
GROUNDEDNESS_TAU: float = 0.45       # cross-encoder threshold for validation gate
DEDUP_TAU: float = 0.85              # cosine similarity ceiling for dedup gate
OPTION_LENGTH_BAND: float = 0.40     # ±40% MCQ option length
MCQ_SHUFFLE_SEED: int = 42
MAX_REGENERATION_PASSES: int = 1     # R5: cap enforced in code, not just comment

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
CHAT_MAX_TURNS: int = 6              # L6: older turns dropped, never summarised

# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL: str = "gemini-2.0-flash-lite"
LLM_TIMEOUT_SECONDS: int = 120       # L7: generous but bounded
MAX_RETRIES: int = 2                 # L1, R5: 1 original + 2 retries = 3 attempts
RETRY_BASE_DELAY_SECONDS: float = 1.0
RETRY_MAX_DELAY_SECONDS: float = 30.0

# ---------------------------------------------------------------------------
# Budget guard (R5, §12.1)
# ---------------------------------------------------------------------------
PER_EXAM_CALL_CAP: int = 20
PER_EXAM_TOKEN_CAP: int = 60_000
PER_DAY_CALL_CAP: int = int(os.getenv("PER_DAY_CALL_CAP", "900"))
