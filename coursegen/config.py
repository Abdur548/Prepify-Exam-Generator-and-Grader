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

# DOCX heading detection. Unlike PDF (relative font size) and like PPTX (placeholder
# type), this is EXACT rather than inferred: Word stores a real paragraph style, and
# every built-in heading level is named "Heading 1" … "Heading 9". Prefix-matched, so
# one constant covers all nine levels. "TOC Heading" and "Title" deliberately do not
# match — neither opens a content section.
DOCX_HEADING_STYLE_PREFIX: str = "Heading"

# Serialisation policy for course_map.json. P1's gate is "identical course_map.json
# hash across two ingests", which is unachievable without a fixed float-rounding and
# key-ordering policy: raw float repr and dict insertion order both vary.
COURSE_MAP_FLOAT_PRECISION: int = 6   # decimal places instructional_mass is rounded to before serialisation
JSON_SORT_KEYS: bool = True           # deterministic key order when persisting JSON

# Cheap character→token estimate used for CourseMapNode.token_count, which feeds
# instructional_mass. This is NOT a tokenizer: it is a constant divisor over the
# section's character count. It is good enough because instructional_mass only ever
# uses token_count RELATIVELY (each node's share of the corpus total), so a uniform
# scaling error cancels out. Replace with a real tokenizer only if an absolute token
# budget is ever computed from this number.
CHARS_PER_TOKEN_ESTIMATE: int = 4

# ---------------------------------------------------------------------------
# Content flag heuristics (NodeFlags.has_code / has_equation)
# ---------------------------------------------------------------------------
# These are HEURISTICS, not detectors. They are matched case-insensitively as
# substrings of the font name reported by the parser (PyMuPDF span "font",
# python-pptx run.font.name).

# Monospace font families → has_code. Monospace is the only signal available in the
# pinned stack; prose set in a monospace face will false-positive.
MONOSPACE_FONT_SUBSTRINGS: tuple[str, ...] = (
    "mono",          # DejaVu Sans Mono, Liberation Mono, Roboto Mono, PT Mono, …
    "courier",
    "consolas",
    "menlo",
    "inconsolata",
    "sourcecodepro",
    "fira code",
    "firacode",
    "cascadia",
)

# Dedicated mathematics font families → has_equation. CMMI/CMSY/CMEX are TeX's
# Computer Modern math fonts and appear in essentially every LaTeX-produced PDF that
# contains mathematics; "Cambria Math" is Word's equation font.
#
# "Symbol" is DELIBERATELY EXCLUDED even though it is a plausible-looking candidate:
# Word sets its default list bullet (U+F0B7) in the Symbol face, so any bulleted
# Word-exported deck would flag every bulleted section as containing an equation.
MATH_FONT_SUBSTRINGS: tuple[str, ...] = (
    "cmmi",            # Computer Modern Math Italic
    "cmsy",            # Computer Modern Symbol
    "cmex",            # Computer Modern Extension
    "mathematicalpi",
    "cambria math",
    "cambriamath",
    "latinmodernmath",
    "lmmath",
    "stixmath",
    "xitsmath",
    "asanamath",
    "euclidmath",
)

# Unicode blocks counted as "mathematical" for the text-based half of the
# has_equation heuristic. Deliberately conservative — blocks that carry mathematics
# and little else. NOT included, and why:
#   Greek (U+0370–U+03FF)          — Greek prose and ordinary words ("alpha release")
#   Arrows (U+2190–U+21FF)         — slide bullets ("Input → Output")
#   Letterlike (U+2100–U+214F)     — contains ™ and ©
#   ± × ÷ (Latin-1 singletons)     — "1920×1080", "±5%"
MATH_UNICODE_RANGES: tuple[tuple[int, int], ...] = (
    (0x2070, 0x209F),    # Superscripts and Subscripts
    (0x2200, 0x22FF),    # Mathematical Operators
    (0x27C0, 0x27EF),    # Miscellaneous Mathematical Symbols-A
    (0x2980, 0x29FF),    # Miscellaneous Mathematical Symbols-B
    (0x2A00, 0x2AFF),    # Supplemental Mathematical Operators
    (0x1D400, 0x1D7FF),  # Mathematical Alphanumeric Symbols
)
# Minimum number of MATH_UNICODE_RANGES characters in one block before that block is
# flagged has_equation. An absolute count, not a ratio: a ratio makes a three-character
# block ("x≤y") look denser than a real displayed equation inside a paragraph.
# 3 is chosen so a single inline symbol in prose ("where x ≤ 5") does not trip it.
EQUATION_MIN_MATH_CHARS: int = 3

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
# UNIT: raw cross-encoder logit, NOT a 0-1 similarity. CALIBRATED 2026-08-29 against
# the real model rather than reasoned about: CrossEncoder.default_activation_function is
# Identity (no sigmoid), a perfect match scores +9.48 and nonsense -11.23.
#
# Measured top-1 scores over a lecture-slide-shaped corpus:
#
#   genuinely answerable queries   +1.68 .. +7.97   (weakest: +1.68)
#   near-miss queries              -11.40 .. -5.99  (strongest: -5.99)
#     — same discipline, adjacent vocabulary, absent from the corpus. These, not
#       cricket and tomatoes, are the real decision boundary; far-irrelevant queries
#       all sat below -10.9 and never came close to mattering.
#
# So the boundary lies in (-5.99, +1.68) and -2.0 sits near its midpoint: ~4.0 of margin
# above the strongest near-miss, ~3.7 below the weakest genuine match. The previous 0.5
# also classified this set perfectly, but was badly placed — 6.5 of margin on one side
# and 1.2 on the other, so a paraphrased or lightly-covered question scoring +0.3 would
# have been wrongly told "not from your material".
#
# Re-check against the real course deck: near-miss scores rise as a corpus covers more
# adjacent topics, and that ceiling is what this threshold has to clear.
RERANKER_THRESHOLD: float = -2.0
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
# UNIT: raw cross-encoder logit from ms-marco-MiniLM-L-6-v2 (unbounded, roughly
# -11 to +11), NOT a 0-1 similarity. STILL UNCALIBRATED — must be set from measured
# logits before it means anything. Do not adjust it without measurement.
#
# DO NOT COPY RERANKER_THRESHOLD (-2.0) HERE. Same model, different task, different
# score distribution. RERANKER_THRESHOLD scores a QUESTION against a candidate chunk;
# this scores a generated ANSWER against the span it was written from. An answer
# derived from its own source is far more similar than a question is to a passage, so
# grounded pairs will cluster much higher — a threshold borrowed from the retrieval
# distribution would pass essentially everything and the gate would stop gating.
# Calibrate it the same way: score genuinely-grounded answers against their spans,
# score hallucinated answers against the same spans, and put the value between them.
GROUNDEDNESS_TAU: float = 0.45
DEDUP_TAU: float = 0.85              # UNIT: cosine similarity in [-1, 1]. Genuine similarity — correct as-is.
OPTION_LENGTH_BAND: float = 0.40     # ±40% MCQ option length
# Base seed for MCQ option shuffling. It is combined with the item's slot_id per
# item — seeding a fresh Random with this constant alone gives EVERY question the
# same permutation, so an LLM's habit of emitting the correct answer first puts the
# answer in the same position on every question in the paper.
MCQ_SHUFFLE_SEED: int = 42
# Option labels assigned by position after shuffling, so the rendered order and the
# answer key cannot disagree regardless of how the renderer prints them.
MCQ_OPTION_LABELS: str = "ABCDEFGH"
MAX_REGENERATION_PASSES: int = 1     # R5: cap enforced in code, not just comment

# ---------------------------------------------------------------------------
# Topic → node matching (Spec Amendment 01, stage 1)
# ---------------------------------------------------------------------------
# An authored blueprint section may name its own `topic`; the solver matches that
# free text against each node's `path` + `key_terms` to build the candidate set.
# Both sides are tokenised the same way: case-folded, split on non-alphanumeric,
# tokens shorter than this dropped. The length threshold is doing the work a
# stopword list would ("of", "a", "3.2", "A*" all fall out) — there is no stopword
# list in the pinned stack and matching must not add a dependency for one.
TOPIC_MATCH_MIN_TOKEN_LEN: int = 3
# UNIT: fraction of the TOPIC's own tokens found in the node, in [0, 1].
# Deliberately NOT Jaccard: the question is "does this node cover the topic", not
# "are these the same size", so a long node must not be penalised for having many
# tokens. UNCALIBRATED — 0.3 was chosen by inspection, not measured, and until it
# is set against a real ingested course deck it does not mean anything. Do NOT
# tune it against the synthetic test fixtures: they are not representative, and a
# number fitted to them would look measured while meaning nothing.
TOPIC_MATCH_MIN_SCORE: float = 0.3

# ---------------------------------------------------------------------------
# Authored blueprint structure (Spec Amendment 01, stage 2)
# ---------------------------------------------------------------------------
# §8.1: the new question formats do NOT widen `item_type`. `item_type` stays
# mcq|short|long because it drives renderer layout AND which validation gates
# apply — gate 4 (MCQ hygiene) keys off `item_type == "mcq"`. A true/false item is
# therefore item_type="mcq", options_count=2, format_requirement="TRUE_FALSE_SERIES":
# hygiene still applies, and the prompt gets the finer instruction.
#
# A SET, deliberately NOT a typing.Literal: adding a format is then a data change
# in this file rather than a code change in the contract. `SectionSpec` validates
# against it and RAISES on an unknown value — a typo'd format that fell through
# would generate a generic question under a blueprint that looked honoured, which
# is exactly the plausible-but-wrong artifact this project keeps producing.
KNOWN_FORMAT_REQUIREMENTS: frozenset[str] = frozenset({
    "TRUE_FALSE_SERIES",
    "ALGORITHMIC_TRACE_PROBLEM",
    "SCENARIO_MODELING_AND_SOLVING",
    "MATHEMATICAL_MODELING",
    "ANALYTICAL_SHORT_ANSWER",
})

# UNIT: absolute difference between two proportions, each in [0, 1] — so 0.10 is
# ten percentage points, NOT ten percent of the target.
#
# A blueprint may declare `cognitive_balance`, the exam-level Bloom mix its tasks
# are meant to sum to. The REALISED mix is not knowable at parse time — it depends
# on how many slots each section actually filled — so it is compared in the
# CoverageReport and divergence beyond this is a WARNING, never a raise: an
# under-filled paper legitimately misses its target, and that is information.
#
# A STARTING VALUE, not a measured one. Nothing has been calibrated against real
# papers yet; it was picked so that a one-question wobble on a 20-question section
# does not cry wolf while a paper drifting from 70% apply/analyse to 40% does.
COGNITIVE_BALANCE_TOLERANCE: float = 0.10

# ---------------------------------------------------------------------------
# Grounding mode (Spec Amendment 01, stage 3)
# ---------------------------------------------------------------------------
# UNIT: fraction of the EMITTED items in one paper that carry
# grounding="synthesis", in [0, 1]. Above this, generation WARNS.
#
# Why this is worth a number at all: a synthesis item is a question NOT backed
# by the student's own material. For an algorithmic trace that is pedagogically
# correct — an exam should not reuse the game tree from the slides — and
# everywhere else it is an unwelcome surprise, because a student cannot revise a
# novel game tree from their own upload (§7). The count and the ratio therefore
# go in the run_manifest whatever their value, exactly as `fill_ratio` and
# `allocation_fidelity` made earlier invisible degradations visible; this
# constant only decides when the manifest also SAYS something about it.
#
# A STARTING VALUE, not a measured one. Nothing has been calibrated against real
# papers. 0.25 was picked so that one trace question in a five-question section
# does not cry wolf, while a paper that is mostly invented content does. It is a
# WARNING and never a raise: a blueprint may legitimately be trace-heavy, and
# that is information rather than an error.
SYNTHESIS_ITEM_WARN_RATIO: float = 0.25

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
