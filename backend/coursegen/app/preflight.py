"""
Pre-flight checks — R8.

run_preflight_checks() verifies all conditions before the server starts.
A failure at startup produces a legible message rather than a mid-demo crash.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from coursegen import config
from coursegen.exam.render import check_weasyprint_available


def run_preflight_checks(output_dir: Optional[Path] = None) -> None:
    """
    Verify every startup requirement. Raises RuntimeError with a human-readable
    message for the first condition that fails.

    Called once at app startup and exposed at GET /api/preflight for health checks.
    """
    output_dir = output_dir or config.OUTPUT_DIR

    _check_api_key()
    _check_output_dir(output_dir)
    _check_models_present()
    check_weasyprint_available()
    _check_qdrant_openable()


def preflight_status(output_dir: Optional[Path] = None) -> dict[str, str]:
    """Return pass/fail status for every check. Never raises."""
    output_dir = output_dir or config.OUTPUT_DIR
    checks: dict[str, tuple[str, object]] = {
        "api_key": ("API key (GEMINI_API_KEY)", _check_api_key),
        "output_dir": ("output directory writable", lambda: _check_output_dir(output_dir)),
        "models": ("embedding + reranker models on disk", _check_models_present),
        # Ordered after `models` deliberately: "the files are here" and "this
        # machine can actually run them" are different questions, and only the
        # first was ever asked.
        "memory": ("memory headroom for BGE-M3", _check_memory_headroom),
        "weasyprint": ("WeasyPrint / GTK runtime", check_weasyprint_available),
        "qdrant": ("Qdrant openable", _check_qdrant_openable),
    }
    result: dict[str, str] = {}
    for key, (label, fn) in checks.items():
        try:
            fn()
            result[key] = "ok"
        except Exception as exc:
            result[key] = str(exc)
    return result


def _check_api_key() -> None:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env (see .env.example)."
        )


def _check_output_dir(output_dir: Path) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".preflight_probe"
        probe.write_text("ok")
        probe.unlink()
    except (OSError, PermissionError) as exc:
        raise RuntimeError(
            f"Output directory {output_dir} is not writable: {exc}"
        ) from exc


def _free_virtual_memory_gb() -> Optional[float]:
    """Free virtual memory in GB, or None where it cannot be determined.

    None is not a failure. A machine whose memory cannot be read is not a machine
    that is out of memory, and a preflight that fails closed on an unknown would
    block startup on every platform this helper does not cover.
    """
    if os.name == "nt":
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        # AvailPageFile is committable memory: RAM plus room in the page file.
        # That is the quantity OSError 1455 is complaining about, not free RAM.
        return status.ullAvailPageFile / (1024 ** 3)

    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return (pages * size) / (1024 ** 3)
    except (ValueError, AttributeError, OSError):
        return None


def _check_memory_headroom() -> None:
    """Refuse to promise the models will run when they provably will not.

    Preflight already reports whether the model FILES are on disk. That check
    passed on 2026-09-01 on a machine where BGE-M3 could not complete a forward
    pass -- so preflight said "ok" about a model that would kill the process. A
    check that reports a system as ready when it is not is worse than no check,
    because it is believed.

    This does not guarantee the models will run. It catches the one condition
    already observed to kill them.
    """
    free = _free_virtual_memory_gb()
    if free is None:
        return
    if free < config.MIN_FREE_VIRTUAL_MEMORY_GB:
        raise RuntimeError(
            f"only {free:.2f} GB of committable memory free; BGE-M3 needs about "
            f"{config.MIN_FREE_VIRTUAL_MEMORY_GB:.1f} GB. Ingest and chat will kill "
            f"the process (Windows access violation, not a catchable error). "
            f"Free memory, or raise the page file -- a manually capped page file is "
            f"the usual cause."
        )


def _check_models_present() -> None:
    """
    R8 lists "models present on disk" among the startup checks, and it is the one
    that bites hardest on a cold machine: without it the server starts perfectly,
    ingest may even work if one model happens to be cached, and the failure surfaces
    on the first chat query or the first groundedness gate — mid-demo, which is
    exactly what R8 exists to prevent.

    Not hypothetical: `cross-encoder/ms-marco-MiniLM-L-6-v2` was absent from the
    development machine until 2026-08-29, so the groundedness gate and the chat
    reranker had only ever run against injected test stubs.

    Cache-only lookup — this makes no network call, so a preflight never blocks on a
    download, and an offline machine is reported as missing rather than hanging.
    """
    from huggingface_hub import try_to_load_from_cache

    missing = [
        repo for repo in (config.EMBEDDING_MODEL, config.RERANKER_MODEL)
        if not isinstance(
            try_to_load_from_cache(repo_id=repo, filename="config.json"), str
        )
    ]
    if missing:
        raise RuntimeError(
            "Required model(s) not present on disk: "
            + ", ".join(missing)
            + ". They are downloaded on first use, which would happen mid-run; fetch "
            "them ahead of time on this machine before relying on the app."
        )


def _check_qdrant_openable() -> None:
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(path=config.QDRANT_PATH)
        client.close()
    except Exception as exc:
        # S8: local-mode Qdrant takes an EXCLUSIVE file lock, so the usual cause of
        # this on a working install is a second uvicorn worker. Naming that here turns
        # what the PRD calls "a random failure on the demo machine" into a
        # self-diagnosing one — the README says --workers 1, but documentation does
        # not stop anyone typing --workers 4.
        raise RuntimeError(
            f"Qdrant not openable at {config.QDRANT_PATH}: {exc}. "
            "Local-mode Qdrant holds an exclusive file lock, so if the server is "
            "already running — or was started with more than one uvicorn worker — "
            "this is expected. Run with `--workers 1` (S8)."
        ) from exc
