import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FakeRoute } from "../test/fakeBackend";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
  openExternalUrl: vi.fn(),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

const WAIT = { timeout: 5000 };

const row = (id: number, name: string, parent: number | null) => ({
  id,
  parent_id: parent,
  name,
  code: `LOC-${String(id).padStart(6, "0")}`,
  notes: null,
  archived: false,
  own_items: 0,
  rolled_up_items: 0,
  needs_attention: false,
});

// Stockroom > (Shelf A, Shelf B) ; Workshop ; Old shed (archived)
const TREE = {
  locations: [
    row(1, "Stockroom", null),
    row(2, "Shelf A", 1),
    row(3, "Shelf B", 1),
    row(4, "Workshop", null),
    { ...row(5, "Old shed", null), archived: true },
  ],
  total_locations: 4,
  attention_locations: 0,
  empty_locations: 4,
  unplaced_items: 0,
  placeable_items: 0,
};

function renderAt(url: string, routes: FakeRoute[] = []) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: "/locations", respond: () => TREE },
    ...routes,
    { method: "GET", path: /^\/orders\?/, respond: () => ({ items: [], total: 0, limit: 1, offset: 0 }) },
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  setRoutes([]);
  window.localStorage.clear();
});

describe("Print labels", () => {
  it("opens with the locations it was sent, one label each, at the default 51 x 25 mm", async () => {
    renderAt("/location-labels?ids=2,3");
    expect(await screen.findByRole("heading", { name: "Print labels" }, WAIT)).toBeInTheDocument();
    expect(await screen.findByText(/2 labels · one per page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Print 2 labels" })).toBeEnabled();
    expect(screen.getByText(/paper size should be set to 51 × 25 mm/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "51 × 25 mm", pressed: true })).toBeInTheDocument();
  });

  it("each label shows where its location sits, never the archived ones", async () => {
    renderAt("/location-labels?ids=2");
    await screen.findByText(/1 label · one per page/);
    // The on-screen preview, plus the hidden copy that is the only thing a print shows.
    expect(screen.getAllByText("Stockroom").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("img", { name: "Code 128 barcode for LOC-000002" }).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Old shed")).not.toBeInTheDocument();
  });

  it("including everything beneath a tick adds the locations inside it", async () => {
    renderAt("/location-labels?ids=1");
    await screen.findByText(/1 label · one per page/);
    fireEvent.click(screen.getByLabelText("Include everything beneath each tick"));
    expect(await screen.findByText(/3 labels · one per page/)).toBeInTheDocument();
  });

  it("with nothing ticked there is nothing to print", async () => {
    renderAt("/location-labels");
    expect(await screen.findByText(/0 labels · one per page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Print 0 labels" })).toBeDisabled();
    expect(screen.getByText("Tick a location to see its label.")).toBeInTheDocument();
  });

  it("switching to 6 x 4 in is remembered on this computer", async () => {
    renderAt("/location-labels?ids=2");
    await screen.findByText(/1 label · one per page/);
    fireEvent.click(screen.getByRole("button", { name: "6 × 4 in" }));
    await waitFor(() => expect(screen.getByText(/paper size should be set to 6 × 4 in/)).toBeInTheDocument());
    expect(JSON.parse(window.localStorage.getItem("stocksmith-label-size")!).preset).toBe("large");
  });

  it("a custom size takes width and height in millimetres and stays in range", async () => {
    renderAt("/location-labels?ids=2");
    await screen.findByText(/1 label · one per page/);
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    const width = await screen.findByLabelText("Width");
    fireEvent.change(width, { target: { value: "70" } });
    fireEvent.blur(width);
    const height = screen.getByLabelText("Height");
    fireEvent.change(height, { target: { value: "5" } }); // too small to hold a barcode
    fireEvent.blur(height);
    await waitFor(() => expect(screen.getByText(/paper size should be set to 70 × 20 mm/)).toBeInTheDocument());
  });

  it("A4 sheet mode tiles the labels on A4 pages, and is unavailable for a label too big to tile", async () => {
    renderAt("/location-labels?ids=1,2,3,4");
    await screen.findByText(/4 labels · one per page/);
    fireEvent.click(screen.getByLabelText(/Several to a page on A4/));
    // 51 x 25 mm: 3 across (190 / 51) and 11 down (277 / 25), so 33 to a page.
    expect(await screen.findByText(/4 labels · 33 per A4 page/)).toBeInTheDocument();
    const css = document.body.querySelector(".location-label-print style")?.textContent ?? "";
    expect(css).toMatch(/@page \{ size: A4; margin: 10mm; \}/);
    expect(document.body.querySelectorAll(".location-label-print .label-sheet")).toHaveLength(1);
    expect(document.body.querySelectorAll(".location-label-print .label-cell")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "6 × 4 in" }));
    await waitFor(() => expect(screen.getByLabelText(/Several to a page on A4/)).toBeDisabled());
    expect(await screen.findByText(/4 labels · one per page/)).toBeInTheDocument();
  });

  it("the print stylesheet sets the page to the label size and hides the rest of the app", async () => {
    renderAt("/location-labels?ids=2");
    await screen.findByText(/1 label · one per page/);
    const css = document.body.querySelector(".location-label-print style")?.textContent ?? "";
    expect(css).toContain("@page { size: 51mm 25mm; margin: 0; }");
    expect(css).toContain("body > *:not(.location-label-print) { display: none !important; }");
  });
});

const detail = (id: number, name: string, path: { id: number; name: string }[]) => ({
  ...row(id, name, null),
  path,
  contents: [],
  children: [],
  history: [],
  history_count: 0,
});
const DETAILS: FakeRoute[] = [
  { method: "GET", path: "/locations/1", respond: () => detail(1, "Stockroom", [{ id: 1, name: "Stockroom" }]) },
  {
    method: "GET",
    path: "/locations/3",
    respond: () => detail(3, "Shelf B", [{ id: 1, name: "Stockroom" }, { id: 3, name: "Shelf B" }]),
  },
];

describe("Scanning a location code", () => {
  it("jumps to the location whose code was typed or scanned", async () => {
    renderAt("/locations", DETAILS);
    const box = await screen.findByLabelText("Scan or type a location code", {}, WAIT);
    await screen.findByRole("heading", { name: "Stockroom" }, WAIT);
    fireEvent.change(box, { target: { value: "loc-000003" } });
    fireEvent.submit(box.closest("form")!);
    expect(await screen.findByRole("heading", { name: "Shelf B" }, WAIT)).toBeInTheDocument();
  });

  it("says so when no location has that code", async () => {
    renderAt("/locations", DETAILS);
    const box = await screen.findByLabelText("Scan or type a location code", {}, WAIT);
    fireEvent.change(box, { target: { value: "LOC-999999" } });
    fireEvent.submit(box.closest("form")!);
    expect(await screen.findByText("No location with that code")).toBeInTheDocument();
  });
});

describe("Settings > Labels", () => {
  it("sets the same saved size the print screen uses", async () => {
    renderAt("/settings?page=labels");
    const card = (await screen.findByRole("heading", { name: "Labels", level: 2 }, WAIT)).closest("section")!;
    fireEvent.click(within(card).getByRole("button", { name: "6 × 4 in" }));
    expect(JSON.parse(window.localStorage.getItem("stocksmith-label-size")!).preset).toBe("large");
  });
});
