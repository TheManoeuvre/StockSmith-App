import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { MaterialSubstitutesSection } = await import("./MaterialSubstitutesSection");

const MATERIALS = [
  { id: 1, name: "Small box", category: "packaging", is_active: true },
  { id: 2, name: "Medium box", category: "packaging", is_active: true },
  { id: 3, name: "Large box", category: "packaging", is_active: true },
  { id: 4, name: "Sturdy box", category: "packaging", is_active: true },
  { id: 5, name: "PLA Black", category: "filament", is_active: true },
];

const CATEGORIES = [
  {
    id: 1,
    name: "packaging",
    sort_order: 10,
    default_unit: "each",
    consumed_on_failed_build: false,
    auto_kitting_per_order: true,
    show_in_kitting_bom_list: true,
    tracks_colour: false,
    tracks_material_type: false,
    cost_per_kg_display: false,
    usage_count: 1,
    created_at: "2026-01-01T00:00:00Z",
  },
];

const SUBSTITUTES = [
  {
    id: 10,
    material_id: 1,
    substitute_material_id: 2,
    substitute_material_name: "Medium box",
    rank: 0,
    notes: "Close enough size",
    created_by: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 11,
    material_id: 1,
    substitute_material_id: 3,
    substitute_material_name: "Large box",
    rank: 1,
    notes: "Oversized but works",
    created_by: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
];

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MaterialSubstitutesSection materialId={1} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  setRoutes([
    { method: "GET", path: "/materials", respond: () => MATERIALS },
    { method: "GET", path: "/material-categories", respond: () => CATEGORIES },
    { method: "GET", path: "/materials/1/substitutes", respond: () => SUBSTITUTES },
    {
      method: "PATCH",
      path: /^\/materials\/1\/substitutes\/\d+$/,
      respond: (body) => ({ ...SUBSTITUTES[0], ...(body as object) }),
    },
    {
      method: "POST",
      path: "/materials/1/substitutes",
      respond: (body) => ({
        id: 12,
        material_id: 1,
        substitute_material_name: "Large box",
        is_active: true,
        created_by: null,
        created_at: "2026-01-01T00:00:00Z",
        ...(body as object),
      }),
    },
  ]);
});

it("lists existing fallbacks ranked, with their notes", async () => {
  renderSection();
  expect(await screen.findByText("Medium box")).toBeInTheDocument();
  expect(screen.getByText("Close enough size")).toBeInTheDocument();
  expect(screen.getByText("Large box")).toBeInTheDocument();
  expect(screen.getByText("Oversized but works")).toBeInTheDocument();
});

it("swaps rank on both rows when reordering", async () => {
  const user = userEvent.setup();
  renderSection();
  await screen.findByText("Medium box");

  // "Move down" on the first (top-ranked) row.
  const downButtons = screen.getAllByRole("button", { name: "Move down" });
  await user.click(downButtons[0]);

  await waitFor(() => {
    const patches = calls.filter((c) => c.method === "PATCH");
    expect(patches).toHaveLength(2);
  });
  const patches = calls.filter((c) => c.method === "PATCH");
  expect(patches).toEqual(
    expect.arrayContaining([
      { method: "PATCH", path: "/materials/1/substitutes/10", body: { rank: 1 } },
      { method: "PATCH", path: "/materials/1/substitutes/11", body: { rank: 0 } },
    ])
  );
});

it("deactivates a fallback without deleting it", async () => {
  const user = userEvent.setup();
  renderSection();
  await screen.findByText("Medium box");

  const [firstDeactivate] = screen.getAllByRole("button", { name: "Deactivate" });
  await user.click(firstDeactivate);

  await waitFor(() => {
    expect(calls).toContainEqual({
      method: "PATCH",
      path: "/materials/1/substitutes/10",
      body: { is_active: false },
    });
  });
});

it("requires notes before a new fallback can be added", async () => {
  renderSection();
  await screen.findByText("Medium box");

  const addButton = screen.getByRole("button", { name: "+ Add fallback" });
  expect(addButton).toBeDisabled();

  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText(/required/i), "Backup colour match");
  expect(addButton).not.toBeDisabled();

  await user.click(addButton);
  await waitFor(() => {
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts).toHaveLength(1);
  });
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ notes: "Backup colour match" });
});

it("only offers materials from the same category as fallbacks", async () => {
  renderSection();
  await screen.findByText("Medium box");

  const options = screen.getAllByRole("option").map((o) => o.textContent);
  // Sturdy box is the only packaging material that isn't the material itself or already a fallback.
  expect(options.some((t) => t?.includes("Sturdy box"))).toBe(true);
  expect(options.some((t) => t?.includes("PLA Black"))).toBe(false);
});
