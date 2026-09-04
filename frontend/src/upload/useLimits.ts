import { useEffect, useState } from "react";

/**
 * What the server will accept, read from the server.
 *
 * The screen used to hardcode `100 * 1024 * 1024` and the string "100 MB" — two
 * client-side copies of one config value. Change `MAX_FILE_SIZE_BYTES` and the
 * client either rejects files the server would take or accepts files it will
 * refuse, while showing a number that is wrong either way.
 *
 * The defaults below are a floor for the moment before the fetch lands, not a
 * second source of truth: the server enforces the real limits regardless, so the
 * worst case is one file briefly judged by a stale ceiling and then refused with
 * the server's own message.
 */

export interface Limits {
  maxFileBytes: number;
  maxUploadBytes: number;
  extensions: string[];
}

const PENDING: Limits = {
  maxFileBytes: 100 * 1024 * 1024,
  maxUploadBytes: 400 * 1024 * 1024,
  extensions: [".pdf", ".pptx", ".docx"],
};

export function useLimits(): Limits {
  const [limits, setLimits] = useState<Limits>(PENDING);

  useEffect(() => {
    let live = true;
    fetch("/api/limits")
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (!live || !b) return;
        setLimits({
          maxFileBytes: b.max_file_bytes ?? PENDING.maxFileBytes,
          maxUploadBytes: b.max_upload_bytes ?? PENDING.maxUploadBytes,
          extensions: Array.isArray(b.extensions) ? b.extensions : PENDING.extensions,
        });
      })
      .catch(() => {
        /* the server enforces them anyway */
      });
    return () => {
      live = false;
    };
  }, []);

  return limits;
}

/** `104857600` → `100 MB`. One formatter, so the zone and the refusal agree. */
export function mb(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}
