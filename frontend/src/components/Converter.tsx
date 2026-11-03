"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ConversionApiError,
  type ConversionResult,
  discardJob,
  fetchDocument,
  getHealth,
  getStatus,
  type Health,
  sendFeedback,
  type JobStatus,
  type Mode,
  uploadPdf,
} from "@/lib/api";

import { buttonClass, DocIcon, Window } from "./retro";

type Phase = "idle" | "ready" | "uploading" | "processing" | "done" | "error";

interface Download {
  url: string;
  filename: string;
  size: number;
}

const DEFAULT_LIMIT_MB = 25;
const POLL_MS = 600;
const UPLOAD_SHARE = 0.2; // share of the overall bar used by the upload step

const MODES: { value: Mode; title: string; body: string }[] = [
  {
    value: "layout",
    title: "Keep the original layout",
    body: "Matches fonts, sizes, spacing and page breaks as closely as possible.",
  },
  {
    value: "clean",
    title: "Clean and easy to edit",
    body: "Keeps headings, lists, tables and images but uses simple, consistent formatting.",
  },
];

const STAGE_TEXT: Record<string, string> = {
  queued: "Waiting for a free converter",
  opening: "Opening the PDF",
  analysing: "Rebuilding paragraphs, headings and tables",
  writing: "Writing the Word document",
  verifying: "Checking the Word document opens correctly",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function stageText(status: JobStatus | null): string {
  if (!status) return "Starting conversion";
  if (status.stage === "extracting" && status.total_pages) {
    return `Reading page ${status.current_page} of ${status.total_pages}`;
  }
  return STAGE_TEXT[status.stage] ?? "Converting";
}

function validate(file: File, limitMb: number): string | null {
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) return `“${file.name}” isn't a PDF. Choose a file that ends in .pdf.`;
  if (file.size === 0) return "This file is empty. Choose a PDF that has content.";
  if (file.size > limitMb * 1024 * 1024) {
    return `This file is ${formatSize(file.size)}. The limit is ${limitMb} MB — try compressing the PDF or splitting it.`;
  }
  return null;
}

export default function Converter() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<Mode>("layout");
  const [uploadFraction, setUploadFraction] = useState(0);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<ConversionResult | null>(null);
  const [download, setDownload] = useState<Download | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorTitle, setErrorTitle] = useState("Conversion didn't finish");
  const [dragging, setDragging] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const jobRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const downloadRef = useRef<Download | null>(null);

  const limitMb = health?.limits.max_upload_mb ?? DEFAULT_LIMIT_MB;

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealthError(true));
  }, []);

  const stopWork = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (pollRef.current) clearTimeout(pollRef.current);
    pollRef.current = null;
  }, []);

  const releaseDownload = useCallback(() => {
    if (downloadRef.current) URL.revokeObjectURL(downloadRef.current.url);
    downloadRef.current = null;
    setDownload(null);
  }, []);

  // Clean up timers, uploads, object URLs and server-side files when leaving the page.
  useEffect(() => {
    const onLeave = () => {
      if (jobRef.current) discardJob(jobRef.current);
    };
    window.addEventListener("pagehide", onLeave);
    return () => {
      window.removeEventListener("pagehide", onLeave);
      stopWork();
      if (downloadRef.current) URL.revokeObjectURL(downloadRef.current.url);
    };
  }, [stopWork]);

  const fail = useCallback((message: string) => {
    stopWork();
    jobRef.current = null;
    setErrorTitle("Conversion didn't finish");
    setError(message);
    setPhase("error");
  }, [stopWork]);

  const choose = useCallback(
    (picked: File | undefined) => {
      if (!picked) return;
      releaseDownload();
      setResult(null);
      setStatus(null);
      const problem = validate(picked, limitMb);
      setErrorTitle("This file can't be converted");
      setFile(problem ? null : picked);
      setError(problem);
      setPhase(problem ? "error" : "ready");
    },
    [limitMb, releaseDownload],
  );

  const poll = useCallback(
    (id: string, signal: AbortSignal, name: string) => {
      const tick = async () => {
        if (signal.aborted) return;
        try {
          const next = await getStatus(id, signal);
          if (signal.aborted) return;
          setStatus(next);
          if (next.status === "done") {
            const doc = await fetchDocument(id, name);
            if (signal.aborted) return;
            jobRef.current = null; // the server deleted its copy on download
            const url = URL.createObjectURL(doc.blob);
            downloadRef.current = { url, filename: doc.filename, size: doc.blob.size };
            setDownload(downloadRef.current);
            setResult(next.result ?? null);
            setPhase("done");
            return;
          }
          if (next.status === "failed") {
            jobRef.current = null;
            fail(next.error?.message ?? "The conversion failed.");
            return;
          }
          pollRef.current = setTimeout(tick, POLL_MS);
        } catch (err) {
          if (signal.aborted) return;
          fail(err instanceof ConversionApiError ? err.message : "Something went wrong while converting.");
        }
      };
      void tick();
    },
    [fail],
  );

  const convert = useCallback(async () => {
    if (!file) return;
    stopWork();
    releaseDownload();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setStatus(null);
    setResult(null);
    setUploadFraction(0);
    setPhase("uploading");
    try {
      const { id } = await uploadPdf(file, mode, setUploadFraction, controller.signal);
      if (controller.signal.aborted) return;
      jobRef.current = id;
      setPhase("processing");
      poll(id, controller.signal, file.name.replace(/\.pdf$/i, "") + ".docx");
    } catch (err) {
      if (controller.signal.aborted) return;
      fail(err instanceof ConversionApiError ? err.message : "The upload failed. Please try again.");
    }
  }, [file, mode, poll, fail, stopWork, releaseDownload]);

  const cancel = useCallback(() => {
    stopWork();
    if (jobRef.current) discardJob(jobRef.current);
    jobRef.current = null;
    setStatus(null);
    setPhase(file ? "ready" : "idle");
  }, [file, stopWork]);

  const reset = useCallback(() => {
    cancel();
    releaseDownload();
    setFile(null);
    setResult(null);
    setError(null);
    setPhase("idle");
    if (inputRef.current) inputRef.current.value = "";
  }, [cancel, releaseDownload]);

  const busy = phase === "uploading" || phase === "processing";
  const progress =
    phase === "uploading"
      ? uploadFraction * UPLOAD_SHARE
      : phase === "processing"
        ? UPLOAD_SHARE + ((status?.progress ?? 0) / 100) * (1 - UPLOAD_SHARE)
        : phase === "done"
          ? 1
          : 0;
  const statusLine =
    phase === "uploading" ? `Uploading ${Math.round(uploadFraction * 100)}%` : phase === "processing" ? stageText(status) : "";

  return (
    <section aria-label="Convert a PDF" className="space-y-7">
      {healthError && (
        <p role="status" className="border-[3px] border-navy bg-orange px-4 py-3 text-sm font-semibold">
          The conversion service isn&apos;t responding right now. You can still choose a file and try.
        </p>
      )}

      <DropZone
        phase={phase}
        progress={progress}
        dragging={dragging}
        file={file}
        onBrowse={() => inputRef.current?.click()}
        onDragState={setDragging}
        onDropFile={choose}
        limitMb={limitMb}
      />
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => choose(e.target.files?.[0])}
      />

      {file && phase !== "done" && (
        <fieldset disabled={busy}>
          <legend className="mb-3 font-bold">How should the Word document look?</legend>
          <div className="grid gap-4 sm:grid-cols-2">
            {MODES.map((m) => {
              const selected = mode === m.value;
              return (
                <label
                  key={m.value}
                  className={`group block cursor-pointer border-[3px] border-navy bg-paper transition-[transform,box-shadow] duration-100 has-[:focus-visible]:outline-[3px] has-[:focus-visible]:outline-offset-[3px] has-[:focus-visible]:outline-navy ${
                    selected ? "shadow-hard" : "shadow-none hover:shadow-hard-sm"
                  } ${busy ? "cursor-not-allowed opacity-70" : ""}`}
                >
                  <input
                    type="radio"
                    name="mode"
                    value={m.value}
                    checked={selected}
                    onChange={() => setMode(m.value)}
                    className="sr-only"
                  />
                  <span
                    className={`flex items-center justify-between border-b-[3px] border-navy px-3 py-1.5 ${
                      m.value === "layout" ? "bg-yellow" : "bg-pink"
                    }`}
                  >
                    <span className="text-[0.8125rem] font-bold tracking-[0.06em] uppercase">
                      {m.value === "layout" ? "Layout.doc" : "Clean.doc"}
                    </span>
                    <span
                      aria-hidden="true"
                      className={`grid size-5 place-items-center border-[2.5px] border-navy bg-paper ${selected ? "" : "opacity-80"}`}
                    >
                      {selected && <span className="size-2.5 bg-navy" />}
                    </span>
                  </span>
                  <span className="block px-4 py-3">
                    <span className="block font-bold">{m.title}</span>
                    <span className="mt-1 block text-sm leading-snug text-navy-soft">{m.body}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      )}

      <div aria-live="polite">
        {busy && (
          <div className="space-y-2">
            <div
              role="progressbar"
              aria-label="Conversion progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress * 100)}
              className="h-7 w-full border-[3px] border-navy bg-paper p-[3px]"
            >
              <div className="progress-fill h-full" style={{ width: `${progress * 100}%` }} />
            </div>
            <p className="text-sm font-semibold">{statusLine}</p>
          </div>
        )}
        {phase === "error" && error && (
          <Window title="Error" color="orange" role="alert" bodyClassName="px-5 py-4" className="pop">
            <p className="font-bold">{errorTitle}</p>
            <p className="mt-1 leading-snug">{error}</p>
          </Window>
        )}
        {phase === "done" && download && result && (
          <ResultDialog result={result} download={download} onReset={reset} />
        )}
      </div>

      {phase !== "done" && (
        <div className="flex flex-wrap gap-4">
          {(phase === "ready" || (phase === "error" && file)) && (
            <button type="button" onClick={convert} className={buttonClass.yellow}>
              {phase === "error" ? "Try again" : "Convert to Word"}
            </button>
          )}
          {busy && (
            <button type="button" onClick={cancel} className={buttonClass.white}>
              Cancel
            </button>
          )}
          {phase === "error" && (
            <button type="button" onClick={reset} className={buttonClass.white}>
              Choose a different file
            </button>
          )}
        </div>
      )}

      {phase === "done" && result && <FeedbackPrompt key={download?.url} result={result} />}

      {health && !health.ocr_available && (
        <p className="text-sm text-navy-soft">
          Scanned PDFs can&apos;t be turned into editable text on this server because OCR isn&apos;t installed.
        </p>
      )}
    </section>
  );
}

interface DropZoneProps {
  phase: Phase;
  progress: number;
  dragging: boolean;
  file: File | null;
  limitMb: number;
  onBrowse: () => void;
  onDragState: (dragging: boolean) => void;
  onDropFile: (file: File | undefined) => void;
}

/** The upload target: a dashed selection marquee, like dragging a box on a desktop. */
function DropZone({ phase, progress, dragging, file, limitMb, onBrowse, onDragState, onDropFile }: DropZoneProps) {
  const busy = phase === "uploading" || phase === "processing";
  const interactive = !busy;
  const converted = phase === "done";

  return (
    <div
      className={`border-[3px] border-dashed border-navy p-5 transition-colors sm:p-7 ${dragging ? "bg-yellow/40" : "bg-sky/15"}`}
      onDragOver={(e) => {
        if (!interactive) return;
        e.preventDefault();
        onDragState(true);
      }}
      onDragLeave={() => onDragState(false)}
      onDrop={(e) => {
        e.preventDefault();
        onDragState(false);
        if (interactive) onDropFile(e.dataTransfer.files?.[0]);
      }}
    >
      <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
        <div aria-hidden="true" className="flex shrink-0 items-end gap-3">
          <div className="flex flex-col items-center gap-1">
            <DocIcon fill={file ? "#ef72c3" : "#ffffff"} width={46} />
            <span className="text-[0.6875rem] font-bold tracking-[0.06em]">PDF</span>
          </div>
          <svg width="30" height="20" viewBox="0 0 30 20" className="mb-8">
            <path d="M2 10h22M17 3l8 7-8 7" fill="none" stroke="#1d1048" strokeWidth="3" strokeLinecap="square" />
          </svg>
          <div className="flex flex-col items-center gap-1">
            <div className="relative">
              <DocIcon fill={converted ? "#f9d949" : "#ffffff"} width={46} />
              {busy && (
                <span
                  className="absolute bottom-[6px] left-[6px] w-[34px] bg-purple transition-[height] duration-300"
                  style={{ height: `${Math.round(progress * 40)}px` }}
                />
              )}
            </div>
            <span className="text-[0.6875rem] font-bold tracking-[0.06em]">DOCX</span>
          </div>
        </div>

        <div className="min-w-0 flex-1">
          {file && phase !== "idle" ? (
            <>
              <p className="text-sm font-semibold text-navy-soft">{converted ? "Converted" : "Selected PDF"}</p>
              <p className="mt-0.5 text-lg font-bold break-words">{file.name}</p>
              <p className="text-sm text-navy-soft">{formatSize(file.size)}</p>
              {interactive && !converted && (
                <button type="button" onClick={onBrowse} className="mt-3 text-sm font-bold underline decoration-[2.5px] underline-offset-4">
                  Choose a different PDF
                </button>
              )}
            </>
          ) : (
            <>
              <p className="text-xl font-bold">Drop a PDF here</p>
              <p className="mt-0.5 mb-4 text-navy-soft">or pick one from your computer</p>
              <button type="button" onClick={onBrowse} className={buttonClass.yellow}>
                Choose a PDF
              </button>
              <p className="mt-4 text-sm text-navy-soft">One file at a time, up to {limitMb} MB.</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** The success state, styled after the brand art's "Open document ?" dialog. */
function ResultDialog({ result, download, onReset }: { result: ConversionResult; download: Download; onReset: () => void }) {
  const facts = [
    `${result.pages} ${result.pages === 1 ? "page" : "pages"}`,
    result.tables ? `${result.tables} ${result.tables === 1 ? "table" : "tables"}` : null,
    result.images ? `${result.images} ${result.images === 1 ? "image" : "images"}` : null,
    result.ocr_pages ? `${result.ocr_pages} scanned ${result.ocr_pages === 1 ? "page" : "pages"} read with OCR` : null,
  ].filter(Boolean);

  return (
    <Window title="Done" color="pink" bodyClassName="px-5 py-5 sm:px-7" className="pop" role="dialog" labelledBy="result-title">
      <p id="result-title" className="text-2xl font-extrabold tracking-[0.02em] uppercase">Open document?</p>
      <p className="mt-2 leading-snug break-words">
        <span className="font-bold">{download.filename}</span> is ready: {facts.join(", ")}, {formatSize(download.size)}.
      </p>
      {result.warnings.length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-[0.9375rem] leading-snug">
          {result.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <div className="mt-5 flex flex-wrap gap-4">
        <a href={download.url} download={download.filename} className={buttonClass.yellow}>
          Download Word document
        </a>
        <button type="button" onClick={onReset} className={buttonClass.pink}>
          Convert another PDF
        </button>
      </div>
    </Window>
  );
}

type FeedbackState = "ask" | "details" | "sending" | "sent" | "error";

/** One-question survey after a conversion: did it work? Optional comment, no personal data. */
function FeedbackPrompt({ result }: { result: ConversionResult }) {
  const [state, setState] = useState<FeedbackState>("ask");
  const [useful, setUseful] = useState<boolean | null>(null);
  const [comment, setComment] = useState("");

  const submit = async () => {
    if (useful === null) return;
    setState("sending");
    try {
      await sendFeedback({ useful, comment: comment.trim(), mode: result.mode, pages: result.pages });
      setState("sent");
    } catch {
      setState("error");
    }
  };

  return (
    <Window title="Quick question" color="purple" bodyClassName="px-5 py-4">
      {state === "sent" ? (
        <p className="font-semibold">Thanks for the feedback.</p>
      ) : (
        <fieldset disabled={state === "sending"}>
          <legend className="font-bold">After opening the file: did the conversion work for you?</legend>
          <div className="mt-3 flex gap-4">
            {[true, false].map((value) => (
              <button
                key={String(value)}
                type="button"
                aria-pressed={useful === value}
                onClick={() => {
                  setUseful(value);
                  setState("details");
                }}
                className={`${value ? buttonClass.yellow : buttonClass.pink} min-w-24 ${
                  useful === value ? "translate-x-[3px] translate-y-[3px] shadow-none" : ""
                }`}
              >
                {value ? "Yes" : "No"}
              </button>
            ))}
          </div>
          {state !== "ask" && (
            <div className="mt-4 space-y-3">
              <label htmlFor="feedback-comment" className="block font-semibold">
                {useful ? "What did you use it for? (optional)" : "What went wrong? (optional)"}
              </label>
              <textarea
                id="feedback-comment"
                value={comment}
                maxLength={1000}
                rows={3}
                onChange={(e) => setComment(e.target.value)}
                className="block w-full resize-y border-[3px] border-navy bg-paper px-3 py-2"
              />
              <p className="text-sm text-navy-soft">Please don&apos;t include personal or confidential information.</p>
              <button type="button" onClick={submit} className={buttonClass.white}>
                {state === "sending" ? "Sending…" : "Send feedback"}
              </button>
              {state === "error" && (
                <p role="alert" className="text-sm font-semibold">
                  Your feedback couldn&apos;t be sent. Please try again.
                </p>
              )}
            </div>
          )}
        </fieldset>
      )}
    </Window>
  );
}
