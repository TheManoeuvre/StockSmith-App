import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls, FakeApiError } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

const MAKE = { id: 1, name: "Make", station: "Making", kind: "make", per_order: false, wait_minutes: null, typical_minutes: null, created_at: "", route_count: 1, event_count: 0 };
const SHIP = { id: 2, name: "Ship", station: "Packing", kind: "ship", per_order: false, wait_minutes: null, typical_minutes: null, created_at: "", route_count: 1, event_count: 0 };
const MASK = { id: 3, name: "Mask", station: "Bench", kind: "custom", per_order: false, wait_minutes: 20, typical_minutes: null, created_at: "", route_count: 0, event_count: 0 };

function step(stage: typeof MAKE, position: number) {
  return { id: position, stage_id: stage.id, position, mix_variants: false, qty_basis: "unit", qty_category_id: null, stage_name: stage.name, stage_kind: stage.kind, station: stage.station, per_order: stage.per_order };
}

const STANDARD = { id: 10, name: "Standard", is_default: true, created_at: "", steps: [step(MAKE, 1), step(SHIP, 2)], product_count: 0, category_count: 1 };
const COASTER = { id: 11, name: "Coaster", is_default: false, created_at: "", steps: [step(MASK, 1), step(MAKE, 2), step(SHIP, 3)], product_count: 0, category_count: 0 };

let enabled = false;

function backend(extra: Parameters<typeof setRoutes>[0] = []) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: "/work/settings", respond: () => ({ work_flow_enabled: enabled, work_flow_enabled_at: null }) },
    { method: "PATCH", path: "/work/settings", respond: (b) => ({ work_flow_enabled: (b as { work_flow_enabled: boolean }).work_flow_enabled, work_flow_enabled_at: null }) },
    { method: "GET", path: "/work/stages", respond: () => [MAKE, SHIP, MASK] },
    { method: "GET", path: "/work/routes", respond: () => [STANDARD, COASTER] },
    { method: "GET", path: "/product-categories", respond: () => [{ id: 5, name: "Coasters", usage_count: 3, created_at: "", work_route_id: null }] },
    ...extra,
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
}

async function renderSettings() {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/settings?page=workflow"]}) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "Stages" }, { timeout: 5000 });
}

beforeEach(() => {
  enabled = false;
  backend();
});

describe("Settings → Work flow", () => {
  it("starts off, and switching it on tells the server", async () => {
    await renderSettings();
    const box = await screen.findByRole("checkbox", { name: "Use work flow" });
    expect((box as HTMLInputElement).checked).toBe(false);
    await userEvent.click(box);
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/work/settings")?.body).toEqual({ work_flow_enabled: true }));
  });

  it("lists the stages, with the built-in ones undeletable", async () => {
    await renderSettings();
    expect(await screen.findByLabelText("Name of Make")).toBeTruthy();
    expect(screen.getAllByText(/Built in/).length).toBe(2);
    const maskRow = screen.getByLabelText("Name of Mask").closest("tr")!;
    expect(within(maskRow).getByRole("button", { name: "Delete" })).toBeTruthy();
    const makeRow = screen.getByLabelText("Name of Make").closest("tr")!;
    expect(within(makeRow).queryByRole("button", { name: "Delete" })).toBeNull();
    expect((screen.getByLabelText("Mask is per order") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText("Make is per order") as HTMLInputElement).disabled).toBe(true);
  });

  it("adds a stage with its station", async () => {
    backend([{ method: "POST", path: "/work/stages", respond: (b) => ({ ...MASK, id: 9, ...(b as object) }) }]);
    await renderSettings();
    await userEvent.type(await screen.findByLabelText("New stage"), "Sand");
    await userEvent.type(screen.getByLabelText("Station", { selector: "input[placeholder='Bench']" }), "Bench");
    await userEvent.click(screen.getByRole("button", { name: "Add stage" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/work/stages")?.body).toEqual({ name: "Sand", station: "Bench" }));
  });

  it("saves a stage change when you leave the field", async () => {
    backend([{ method: "PATCH", path: "/work/stages/3", respond: () => MASK }]);
    await renderSettings();
    const wait = await screen.findByLabelText("Wait minutes for Mask");
    await userEvent.clear(wait);
    await userEvent.type(wait, "45");
    await userEvent.tab();
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/work/stages/3")?.body).toEqual({ wait_minutes: 45 }));
  });

  it("inserts a new step before Make, and saves the whole route in order", async () => {
    backend([{ method: "PATCH", path: "/work/routes/10", respond: () => STANDARD }]);
    await renderSettings();
    const picker = await screen.findByLabelText("Add a step to Standard");
    await userEvent.selectOptions(picker, "Mask");
    await userEvent.click(within(picker.parentElement!).getByRole("button", { name: "Add" }));
    const save = screen.getAllByRole("button", { name: "Save route" })[0];
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/work/routes/10")).toBeTruthy());
    const steps = (calls.find((c) => c.path === "/work/routes/10")!.body as { steps: { stage_id: number }[] }).steps;
    expect(steps.map((s) => s.stage_id)).toEqual([3, 1, 2]); // Mask, Make, Ship
  });

  it("does not offer to move or remove Ship, and Save starts disabled", async () => {
    await renderSettings();
    await screen.findByLabelText("Add a step to Standard");
    expect(screen.queryByRole("button", { name: "Move Ship up" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove Ship from this route" })).toBeNull();
    for (const button of screen.getAllByRole("button", { name: "Save route" })) expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("switches a step to count by a material category and mixes designs", async () => {
    backend([
      { method: "GET", path: "/material-categories", respond: () => [{ id: 4, name: "Blanks", usage_count: 0 }] },
      { method: "PATCH", path: "/work/routes/11", respond: () => COASTER },
    ]);
    await renderSettings();
    const counts = await screen.findByLabelText("What Mask counts");
    await userEvent.selectOptions(counts, "Blanks in the recipe");
    const coaster = screen.getByLabelText("Name of route Coaster").closest("div")!.parentElement!;
    await userEvent.click(within(coaster).getByLabelText("Mix designs at Make"));
    await userEvent.click(screen.getAllByRole("button", { name: "Save route" })[1]);
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/work/routes/11")).toBeTruthy());
    const steps = (calls.find((c) => c.path === "/work/routes/11")!.body as { steps: Record<string, unknown>[] }).steps;
    expect(steps[0]).toMatchObject({ stage_id: 3, qty_basis: "bom_category", qty_category_id: 4 });
    expect(steps[1]).toMatchObject({ stage_id: 1, mix_variants: true });
  });

  it("shows the server's reason when a route is refused", async () => {
    backend([{ method: "PATCH", path: "/work/routes/11", respond: () => { throw new FakeApiError(400, "Ship has to be the last step in a route."); } }]);
    await renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: "Remove Mask from this route" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Save route" })[1]);
    expect(await screen.findByText("Ship has to be the last step in a route.")).toBeTruthy();
  });

  it("assigns a route to a product category", async () => {
    backend([{ method: "PATCH", path: "/product-categories/5", respond: () => ({}) }]);
    await renderSettings();
    const select = await screen.findByLabelText("Route for Coasters");
    expect(within(select).getByRole("option", { name: "Shop default (Standard)" })).toBeTruthy();
    await userEvent.selectOptions(select, "Coaster");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/product-categories/5")?.body).toEqual({ name: "Coasters", work_route_id: 11 }));
  });

  it("refuses to delete the default route by not offering it", async () => {
    await renderSettings();
    await screen.findByLabelText("Add a step to Standard");
    const standard = screen.getByLabelText("Name of route Standard").closest("div")!.parentElement!;
    expect(within(standard).queryByRole("button", { name: "Delete route" })).toBeNull();
    expect(within(standard).getByText("Shop default")).toBeTruthy();
  });
});
