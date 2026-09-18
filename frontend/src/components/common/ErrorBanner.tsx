export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  // The Tauri http plugin rejects with a plain string when a request fails at the transport
  // level, so a string is a real message and not something to hide behind the fallback.
  const message =
    error instanceof Error ? error.message : typeof error === "string" && error ? error : "Something went wrong.";
  return <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{message}</p>;
}
