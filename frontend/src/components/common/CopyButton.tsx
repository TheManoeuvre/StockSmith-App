import { useEffect, useRef, useState } from "react";

// Copies a value (SKUs, product names) to the clipboard for pasting into a marketplace's
// own listing tools — the reason this exists at all, rather than leaving people to
// highlight the text by hand.
export function CopyButton({
  value,
  label,
  className = "",
}: {
  value: string;
  // Becomes the aria-label, so it stays constant while the icon flips to a tick — a
  // label that changed mid-interaction would re-announce the button as a new control.
  label: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // navigator.clipboard needs a secure context. Tauri v2 serves from *.localhost,
      // which Chromium treats as trustworthy, so this should never be reached in either
      // `vite dev` or the packaged app — but the fallback costs a few lines and a
      // silently non-functional copy button would be worse.
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1200);
  };

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={label}
      title={label}
      className={`shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 ${
        copied ? "text-green-600 hover:text-green-600" : ""
      } ${className}`}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </button>
  );
}

// Inline rather than from an icon set: these two are the only icons the app needs, and
// a dependency for them isn't worth it.
function CopyIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5"
    >
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}
