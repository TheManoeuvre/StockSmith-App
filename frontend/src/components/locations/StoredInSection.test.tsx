import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StoredIn } from "../../api/locations";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { StoredInSection } = await import("./StoredInSection");

const TREE = {
  locations: [
    { id: 1, parent_id: null, name: "Workshop", code: "LOC-000001", notes: null, archived: false, own_items: 0, rolled_up_items: 1, needs_attention: false },
    { id: 2, parent_id: 1, name: "Wax shelf", code: "LOC-000002", notes: null, archived: false, own_items: 1, rolled_up_items: 1, needs_attention: false },
  ],
  total_locations: 2,
  attention_locations: 0,
  empty_locations: 0,
  unplaced_items: 0,
  placeable_items: 1,
};

const stored = (o: Partial<StoredIn>): StoredIn => ({
  name: "Soy wax",
  unit: "kg",
  total: "25",
  placed: "0",
  unassigned: "25",
  drift: "0",
  follows_stock: false,
  placements: [],
  ...o,
});

function serve(s: StoredIn, tree: object = TREE) {
  setRoutes([
    { method: "GET", path: /^\/placements\/stored-in/, respond: () => s },
    { method: "GET", path: "/locations", respond: () => tree },
    { method: "POST", path: /^\/placements\/\d+\/reduce$/, respond: () => undefined },
    { method: "POST", path: /^\/placements\/\d+\/primary$/, respond: () => undefined },
  ]);
}

function renderSection() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StoredInSection item={{ material_id: 1 }} itemName="Soy wax" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  setRoutes([]);
  calls.length = 0;
});

describe("StoredInSection", () => {
  it("A: an unplaced item says so quietly and offers Place", async () => {
    serve(stored({}));
    renderSection();
    expect(await screen.findByText("Not stored anywhere yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Place" })).toBeInTheDocument();
  });

  it("A: with no locations at all it points at the Locations page instead of offering Place", async () => {
    serve(stored({}), { ...TREE, locations: [], total_locations: 0 });
    renderSection();
    expect(await screen.findByRole("link", { name: "Add a location" })).toHaveAttribute("href", "/locations");
    expect(screen.queryByRole("button", { name: "Place" })).not.toBeInTheDocument();
  });

  it("B: one location shows the quantity and says it updates automatically", async () => {
    serve(
      stored({
        placed: "25",
        unassigned: "0",
        follows_stock: true,
        placements: [{ id: 7, location_id: 2, location_path: ["Workshop", "Wax shelf"], qty: "25", is_primary: true }],
      }),
    );
    renderSection();
    expect(await screen.findByText("Workshop › Wax shelf")).toBeInTheDocument();
    expect(screen.getByText("25 kg")).toBeInTheDocument();
    expect(screen.getByText(/Updates automatically/)).toBeInTheDocument();
    expect(screen.queryByText("Unassigned")).not.toBeInTheDocument();
  });

  it("C and D: several locations list the primary, an Unassigned row and a Put away action", async () => {
    serve(
      stored({
        placed: "20",
        unassigned: "5",
        placements: [
          { id: 7, location_id: 2, location_path: ["Workshop", "Wax shelf"], qty: "15", is_primary: true },
          { id: 8, location_id: 1, location_path: ["Loft"], qty: "5", is_primary: false },
        ],
      }),
    );
    renderSection();
    expect(await screen.findByText("Unassigned")).toBeInTheDocument();
    expect(screen.getByText("Primary")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Put away" })).toBeInTheDocument();
    expect(screen.getByText(/aren.t taken from a location automatically/)).toBeInTheDocument();
    expect(screen.queryByText(/Updates automatically/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Make primary" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/placements/8/primary")).toBe(true));
  });

  it("E: drift is amber, plain, and fixed with one click", async () => {
    serve(
      stored({
        total: "3",
        placed: "5",
        unassigned: "0",
        drift: "2",
        placements: [
          { id: 9, location_id: 2, location_path: ["Workshop", "Wax shelf"], qty: "3", is_primary: true },
          { id: 10, location_id: 1, location_path: ["Loft"], qty: "2", is_primary: false },
        ],
      }),
    );
    renderSection();
    expect(await screen.findByText(/Placed 5 kg, but only 3 kg in stock/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Reduce Wax shelf by 2 kg/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/placements/9/reduce")).toBe(true));
    expect(calls.find((c) => c.path === "/placements/9/reduce")?.body).toEqual({ qty: "2" });
  });

  it("renders nothing for an unexpected response rather than breaking the page it sits on", async () => {
    setRoutes([{ method: "GET", path: /.*/, respond: () => [] }]);
    renderSection();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByText("Stored in")).not.toBeInTheDocument();
  });
});
