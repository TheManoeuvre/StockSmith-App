import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlatformSyncSummary } from "../api/platforms";
import { useRefreshOnSync } from "./useRefreshOnSync";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
const { setRoutes } = await import("../test/fakeBackend");

function summary(over: Partial<PlatformSyncSummary> = {}): PlatformSyncSummary {
  return {
    platform: "etsy",
    connected: true,
    last_sync_at: "2026-09-18T09:00:00Z",
    last_sync_status: "success",
    last_sync_error: null,
    failing_push_count: 0,
    blocked_push_count: 0,
    api_calls_today: 0,
    api_call_budget: 5000,
    ...over,
  };
}

const KEY = ["platforms", "sync-summary"];

function renderWatcher(initial: PlatformSyncSummary[]) {
  setRoutes([{ method: "GET", path: "/platforms/sync-summary", respond: () => initial }]);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated: unknown[] = [];
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    invalidated.push(filters?.queryKey);
    return Promise.resolve();
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  // A second observer on the same key, so the test can wait for the hook to have *seen* the
  // first summary — the cache fills before the hook's own observer re-renders, and a poll
  // pushed in that gap would be taken for the mount-time baseline.
  const { result } = renderHook(
    () => {
      useRefreshOnSync();
      return useQuery({ queryKey: KEY, queryFn: () => Promise.reject(new Error("unused")) }).data;
    },
    { wrapper },
  );
  const settled = () => waitFor(() => expect(result.current).toBeDefined());
  // Stand in for the next poll landing, without waiting out the real interval. react-query
  // notifies observers on a setTimeout(0), so yield a macrotask before the caller asserts.
  const poll = (next: PlatformSyncSummary[]) =>
    act(async () => {
      queryClient.setQueryData(KEY, next);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  return { invalidated, poll, settled };
}

describe("useRefreshOnSync", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("does not refresh on the summary it mounts with", async () => {
    const { invalidated, settled } = renderWatcher([summary()]);
    await settled();
    expect(invalidated).toEqual([]);
  });

  it("refreshes the order queries when a running sync finishes", async () => {
    const { invalidated, poll, settled } = renderWatcher([summary({ last_sync_status: "running" })]);
    await settled();

    await poll([summary({ last_sync_status: "running" })]);
    expect(invalidated).toEqual([]);

    await poll([summary({ last_sync_status: "success" })]);
    expect(invalidated).toEqual([["orders"], ["order-counts"], ["dashboard-summary"]]);
  });

  it("refreshes when a whole run started and finished between two polls", async () => {
    const { invalidated, poll, settled } = renderWatcher([summary()]);
    await settled();

    await poll([summary({ last_sync_at: "2026-09-18T09:15:00Z" })]);
    expect(invalidated).toContainEqual(["orders"]);
  });

  it("refreshes after a failed run too — it may have imported orders before failing", async () => {
    const { invalidated, poll, settled } = renderWatcher([summary({ last_sync_status: "running" })]);
    await settled();

    await poll([summary({ last_sync_status: "error", last_sync_error: "boom" })]);
    expect(invalidated).toContainEqual(["orders"]);
  });

  it("ignores an unchanged summary", async () => {
    const { invalidated, poll, settled } = renderWatcher([summary()]);
    await settled();

    await poll([summary()]);
    await poll([summary()]);
    expect(invalidated).toEqual([]);
  });
});
