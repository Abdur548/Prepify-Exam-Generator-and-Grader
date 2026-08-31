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
# -11 to +11), NOT a 0-1 similarity.
#
# PROVISIONAL, raised from 0.45 on 2026-08-30 after the first real generation run.
# 0.45 was a probability-shaped guess on a logit scale and it failed 20 of 22 real
# items, destroying the entire TRUE_FALSE_SERIES section.
#
# Observed values (real run + a probe on a 2,047-char span):
#
#   grounded prose answers, real run      +4.44, +5.42
#   correct concise prose, probe          +6.78
#   correct true/false (stem + answer)    +5.52
#   HALLUCINATED claim (stem + answer)    +2.92
#   hallucinated prose (bare answer)      -3.21
#   unrelated topic                      -11.28
#
# 3.5 is the value that admits every correct sample and rejects every incorrect
# one. Be honest about how thin that is: the nearest correct sample sits 0.94
# above it and the nearest incorrect one 0.58 below — ONE sample per category,
# not a calibration. It needs a labelled set of grounded and hallucinated answers
# over this corpus before it can be trusted; see todo.md.
#
# DO NOT COPY RERANKER_THRESHOLD (-2.0) HERE. Same model, different task, different
# distribution: that one scores a QUESTION against a candidate chunk, this scores a
# ---------------------------------------------------------------------------
# DISPROVEN 2026-08-31. The instrument does not measure what this gate claims.
#
# ms-marco-MiniLM is a RELEVANCE reranker: it scores "would this passage be
# retrieved for this query", NOT "does this passage support this claim". Those
# come apart exactly where a groundedness gate has to work. Measured against one
# real span that states verbatim "Optimal? Yes, if step cost = 1 (like BFS)":
#
#   TRUE,  verbatim in span   "IDS is optimal if step cost = 1"      +4.21
#   TRUE,  verbatim in span   "IDS is slower than BFS"               +3.97
#   TRUE,  verbatim in span   "IDS uses linear space"                -0.33
#   FALSE, contradicts span   "IDS is faster, lower complexity"      +4.84  <-- highest of all
#   FALSE, contradicts span   "IDS uses exponential space"           +2.24
#   FALSE, invented fact      "IDS requires a reached structure"     -8.73
#
# TRUE spans -0.33..+4.21, FALSE spans -8.73..+4.84. The classes OVERLAP; the
# top-scoring claim in the set is false. NO threshold separates them, so TAU is
# not miscalibrated - it is unfalsifiable. The earlier one-sample-per-category
# table that produced 3.5 was measuring topical overlap and reading it as truth.
#
# Cost of 3.5 on the first real paper: it deleted A-01, whose claim is verbatim
# supported by its own span, while it would have PASSED the flat contradiction
# above. It removes correct questions and supplies no factuality protection.
#
# The gate still reliably rejects OFF-TOPIC text (-8.73, -11.28), which is worth
# keeping - as a relevance floor, under an honest name. Real factuality needs a
# different instrument (an NLI/entailment model, or the LLM as verifier).
# Until that lands, do not let the manifest report this as "groundedness".
# ---------------------------------------------------------------------------
GROUNDEDNESS_TAU: float = 3.5

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
# tokens shorter than this dropped. This removes heading numbering ("3.2" → "3",
# "2") and one-letter algorithm names ("A*" → "a"), which is the intended cost.
TOPIC_MATCH_MIN_TOKEN_LEN: int = 3

# English function words, removed from BOTH topic and node tokens before matching.
#
# The length threshold alone is not enough, and raising it is not the fix. Under
# matched IDF mass a function word that happens to be RARE in a small course map is
# weighted UP, not down. Measured on the 20-node fixture: "and" occurs in one
# heading, scores idf 3.351 — over twice TOPIC_MATCH_MIN_EVIDENCE — and on that word
# alone admitted "1.3 Variables and Scope" for the topic "Adversarial Search and
# Minimax with Alpha-Beta Pruning", tying the genuinely relevant "3.2 Binary Search".
# Amendment §7 names this exactly: a topic that half-matches is worse than one that
# does not match at all, because it draws questions from the wrong nodes while
# looking like it worked.
#
# Raising TOPIC_MATCH_MIN_TOKEN_LEN to 4 would kill "and"/"the"/"for" — and also
# "MDP", "CSP", "BFS", "DFS", "ID3", the tokens a technical syllabus leans on hardest.
# Naming the function words is the only option that removes the noise without
# removing the signal.
#
# Structure words only. No subject vocabulary belongs here (C5) — a term that is
# uninformative in one discipline is the whole topic in another.
TOPIC_STOPWORDS: frozenset[str] = frozenset({
    "and", "the", "for", "with", "from", "into", "onto", "its", "their", "his", "her",
    "are", "was", "were", "been", "being", "has", "had", "have", "not", "but", "nor",
    "any", "all", "each", "some", "such", "than", "then", "that", "this", "these",
    "those", "there", "here", "when", "where", "which", "while", "who", "whom",
    "you", "your", "our", "via", "per", "out", "off", "over", "under", "between",
    "using", "used", "use", "based", "about", "also", "how", "why", "what",
})
#
# THE SCORE IS MATCHED IDF MASS, NOT A FRACTION OF THE TOPIC.
#
# Stage 1 shipped `|topic ∩ node| / |topic|` and it PUNISHED SPECIFICITY. Measured
# on tests/fixtures/course_map_sample.json: "Search" scored 1.000 and matched,
# while "Uninformed and Informed Search (BFS, DFS, A*, Heuristics)" — a richer,
# more precise phrasing of the SAME topic — scored 0.286 and matched nothing.
# Every enumerated term the node happens not to carry sits in the denominator and
# dilutes the score, and every topic in `template_ai_fundamentals_v1` is a long
# parenthetical string of exactly that shape. IDF-weighting the same fraction does
# not fix it (measured 0.286 → 0.262, slightly WORSE, because the enumerated terms
# are rare and so weighted UP while unmatched). The denominator was the problem.
#
#     df(t)  = course-map nodes whose (path + key_terms) tokens contain t
#     idf(t) = ln((N + 1) / (df(t) + 1)) + 1        # N = node count; always > 0
#     mass(topic, node) = Σ idf(t) for t in (topic_tokens ∩ node_tokens)
#
# Same measurement under the new rule: "Search" → 3.351, the long phrasing →
# 6.703. Extra enumerated terms can now only ADD evidence.
#
# Admission takes TWO conditions, and needs both:
#
#     best = max mass over the section's candidate nodes
#     if best <= 0:                    no match at all
#     admit node  iff  mass(node) >= TOPIC_MATCH_RELATIVE_FLOOR * best
#                 and  best       >= TOPIC_MATCH_MIN_EVIDENCE
#
# UNIT: dimensionless ratio against the best-matching node's mass, in [0, 1].
# RELATIVE because raw IDF mass scales with corpus size — idf depends on N, so an
# absolute-only threshold calibrated on a 20-node fixture would drift on a 200-node
# course. Ranking against the best match is scale-free.
#
# UNCALIBRATED. 0.5 says "admit nodes within half the best node's evidence"; it was
# reasoned, not measured. Do NOT tune it against the synthetic test fixtures: they
# are a generic CS syllabus, not the real AI deck, and a number fitted to them
# would look measured while meaning nothing (todo.md).
TOPIC_MATCH_RELATIVE_FLOOR: float = 0.5
# UNIT: absolute matched IDF mass, in nats — the same units as one idf(t) term,
# so it is read as "how distinctive must the best match's evidence be".
#
# ABSOLUTE because a purely relative rule always admits the best node, however
# weak: `mass >= 0.5 * best` is trivially true for the argmax. This is the guard
# against "best of a bad lot", and it is the only thing standing between a topic
# the upload does not cover and a section quietly filled from the wrong nodes.
#
# UNCALIBRATED. 1.5 is roughly the idf of a term appearing in half the corpus
# (N=20, df=10 → 1.647), i.e. "require at least one matched term more distinctive
# than 'half the course mentions it'". Reasoned, not measured, and NOT fitted to
# the fixtures — same warning as above (todo.md).
TOPIC_MATCH_MIN_EVIDENCE: float = 1.5

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
# Updated 2026-08-30 on the first real API call. `gemini-2.0-flash-lite` — pinned
# since P0 and never once exercised, because every test mocks the client — has been
# RETIRED. The provider returned:
#   "This model models/gemini-2.0-flash-lite is no longer available. Please update
#    your code to use models/gemini-3.5-flash-lite"
#
# UNVERIFIED, and it matters: PER_DAY_CALL_CAP and the whole ~250-exams/day estimate
# come from §6's reading of the 2.0 Flash-Lite free tier (~15 RPM / ~1,000 RPD,
# request-bound with effectively unbounded tokens). Nobody has checked whether 3.5
# Flash-Lite has the same shape. If it is token-bound instead, §6's rationale for
# choosing this provider stops holding — check the live quota page before relying on
# the number, and before any demo.
GEMINI_MODEL: str = "gemini-3.5-flash-lite"
# Cap on how much of a provider error body is quoted into an exception. The
# body carries the only actionable diagnostic (see llm/client.py::_send), but a
# large HTML error page must not flood a log or a degraded-mode message.
ERROR_BODY_MAX_CHARS: int = 500

# Cap on a single validation-issue message stored in the run manifest. Schema
# errors from pydantic can run to thousands of characters; the manifest is a
# diagnostic record, not a log sink.
ISSUE_MESSAGE_MAX_CHARS: int = 300
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
