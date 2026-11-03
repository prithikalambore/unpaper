/** Client for the conversion API (served same-origin through /api/*). */

export type Mode = "layout" | "clean";

export interface ApiError {
  code: string;
  message: string;
}

export class ConversionApiError extends Error {
  code: string;
  constructor({ code, message }: ApiError) {
    super(message);
    this.code = code;
  }
}

export interface Health {
  status: string;
  ocr_available: boolean;
  limits: { max_upload_mb: number; max_pages: number };
}

export interface ConversionResult {
  pages: number;
  mode: Mode;
  ocr_pages: number;
  tables: number;
  images: number;
  characters: number;
  warnings: string[];
}

export interface JobStatus {
  id: string;
  status: "queued" | "processing" | "done" | "failed";
  stage: string;
  progress: number;
  current_page: number;
  total_pages: number;
  filename: string;
  result?: ConversionResult;
  error?: ApiError;
}

const NETWORK_ERROR: ApiError = {
  code: "network_error",
  message: "Couldn't reach the conversion service. Check your connection and try again.",
};

async function parseError(res: Response): Promise<ConversionApiError> {
  try {
    const body = await res.json();
    if (body?.error?.message) return new ConversionApiError(body.error);
  } catch {
    /* fall through */
  }
  return new ConversionApiError({ code: `http_${res.status}`, message: `The server responded with an error (${res.status}).` });
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store", ...init });
  } catch {
    throw new ConversionApiError(NETWORK_ERROR);
  }
  if (!res.ok) throw await parseError(res);
  return res;
}

/** Loads limits and OCR status; also counts this page view (anonymously) when stats are enabled. */
export async function getHealth(): Promise<Health> {
  return (await request("/api/health?visit=1")).json();
}

export async function sendFeedback(feedback: { useful: boolean; comment: string; mode: Mode; pages: number }) {
  await request("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(feedback),
  });
}

/** Upload with progress reporting (fetch cannot report upload progress, XHR can). */
export function uploadPdf(
  file: File,
  mode: Mode,
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
): Promise<{ id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("mode", mode);
    form.append("file", file, file.name);

    xhr.open("POST", "/api/convert");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      const body = xhr.response;
      if (xhr.status >= 200 && xhr.status < 300 && body?.id) resolve({ id: body.id });
      else if (body?.error?.message) reject(new ConversionApiError(body.error));
      else reject(new ConversionApiError({ code: `http_${xhr.status}`, message: `Upload failed (${xhr.status}).` }));
    };
    xhr.onerror = () => reject(new ConversionApiError(NETWORK_ERROR));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

export async function getStatus(id: string, signal?: AbortSignal): Promise<JobStatus> {
  return (await request(`/api/jobs/${encodeURIComponent(id)}`, { signal })).json();
}

function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* use the plain form */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(disposition);
  return plain ? plain[1] : null;
}

/** Fetch the finished document. The server deletes its copy after this request. */
export async function fetchDocument(id: string, fallbackName: string): Promise<{ blob: Blob; filename: string }> {
  const res = await request(`/api/jobs/${encodeURIComponent(id)}/download`);
  const blob = await res.blob();
  if (blob.size === 0) {
    throw new ConversionApiError({ code: "empty_download", message: "The converted document was empty, so it wasn't saved." });
  }
  return { blob, filename: filenameFrom(res.headers.get("content-disposition")) ?? fallbackName };
}

/** Best-effort removal of a job's files on the server (used on cancel and reset). */
export function discardJob(id: string): void {
  fetch(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE", keepalive: true }).catch(() => {});
}
