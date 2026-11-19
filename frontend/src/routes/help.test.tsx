import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
  openExternalUrl: vi.fn(),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

function renderAt(url: string) {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() =>
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: /.*/, respond: () => [] },
  ]),
);

it("shows the help index by default, with every section in the sidebar", async () => {
  renderAt("/help");
  expect(await screen.findByRole("heading", { level: 1, name: "StockSmith help" }, { timeout: 5000 })).toBeInTheDocument();
  const nav = screen.getByRole("navigation", { name: "Help pages" });
  expect(within(nav).getByRole("link", { name: "Connecting Square" })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: "Troubleshooting" })).toBeInTheDocument();
});

it("opens a page named in the URL and turns its .md links into in-app links", async () => {
  renderAt("/help?page=connecting-square");
  expect(await screen.findByRole("heading", { level: 1, name: "Connecting Square" }, { timeout: 5000 })).toBeInTheDocument();
  const article = screen.getByRole("article");
  const backupLink = within(article).getByRole("link", { name: "Backups" });
  expect(backupLink.getAttribute("href")).toBe("/help?page=backup-and-restore");
});

it("falls back to the index for an unknown page", async () => {
  renderAt("/help?page=nope");
  expect(await screen.findByRole("heading", { level: 1, name: "StockSmith help" }, { timeout: 5000 })).toBeInTheDocument();
});
