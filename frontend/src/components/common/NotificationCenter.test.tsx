import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

const REVIEW_ALERT = {
  id: 3,
  category: "replacement_parcel_review",
  urgency: "immediate",
  delivery_mode: "immediate",
  title: "Replacement parcel sent for eBay order 26-14962",
  body: "A second shipping label (3.40 GBP) was bought against this order.",
  related_entity_type: "order",
  related_entity_id: 42,
  created_at: new Date().toISOString(),
  read_at: null,
};

beforeEach(() =>
  setRoutes([
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/notifications/unread-count", respond: () => ({ count: 1 }) },
    { method: "GET" as const, path: /^\/notifications\?/, respond: () => ({ items: [REVIEW_ALERT], total: 1 }) },
    { method: "POST" as const, path: "/notifications/3/read", respond: () => ({ ...REVIEW_ALERT, read_at: "now" }) },
    { method: "GET" as const, path: /^\/orders\?/, respond: () => ({ items: [], total: 0 }) },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ]),
);

it("an alert about an order opens that order's Fulfilment tab and marks itself read", async () => {
  const user = userEvent.setup();
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/orders"] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "1 unread notifications" }));
  const row = await screen.findByRole("link", { name: /Replacement parcel sent for eBay order/ });
  await user.click(row);

  await waitFor(() => expect(router.state.location.pathname).toBe("/orders/42"));
  expect(router.state.location.search).toEqual({ tab: "fulfilment" });
  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST" && c.path === "/notifications/3/read")).toBe(true),
  );
});
