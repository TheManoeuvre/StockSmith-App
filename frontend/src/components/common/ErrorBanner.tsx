export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{message}</p>;
}
