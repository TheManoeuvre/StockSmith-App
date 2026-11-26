import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StoredIn } from "../../api/locations";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { PutAwayModal } = await import("./PutAwayModal");

const loc = (id: number, name: string) => ({
  id,
  parent_id: null,
  name,
  code: `LOC-${String(id).padStart(6, "0")}`,
  notes: null,
  archived: false,
  own_items: 0,
  rolled_up_items: 0,
  needs_attention: false,
});
const TREE = {
  locations: [loc(1, "Loft"), loc(2, "Shelf")],
  total_locations: 2,
  attention_locations: 0,
  empty_locations: 0,
  unplaced_items: 0,
  placeable_items: 2,
};
const stored = (o: Partial<StoredIn>): StoredIn => ({
  name: "Soy wax",
  unit: "kg",
  total: "10",
  placed: "0",
  unassigned: "10",
  drift: "0",
  follows_stock: false,
  placements: [],
  ...o,
});

function renderModal(answers: Record<number, StoredIn>, onClose = vi.fn(), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  setRoutes([
    { method: "GET", path: /^\/placements\/stored-in\?material_id=1$/, respond: () => answers[1] },
    { method: "GET", path: /^\/placements\/stored-in\?material_id=2$/, respond: () => answers[2] },
    { method: "GET", path: "/locations", respond: () => TREE },
    { method: "POST", path: "/placements", respond: () => ({}) },
    { method: "PUT", path: "/placements/put-away-preference", respond: () => undefined },
  ]);
  render(
    <QueryClientProvider client={client}>
      <PutAwayModal
        title="Where did this go?"
        items={[
          { ref: { material_id: 1 }, name: "Soy wax", qty: 10 },
          { ref: { material_id: 2 }, name: "Wicks", qty: 20 },
        ]}
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return onClose;
}

beforeEach(() => {
  setRoutes([]);
  calls.length = 0;
});

describe("PutAwayModal", () => {
  it("asks only about items with nowhere to follow, and pre-fills the primary place", async () => {
    renderModal({
      1: stored({}), // nowhere yet
      2: stored({
        name: "Wicks",
        unit: "each",
        unassigned: "50",
        placements: [
          { id: 1, location_id: 2, location_path: ["Shelf"], qty: "5", is_primary: true },
          { id: 2, location_id: 1, location_path: ["Loft"], qty: "5", is_primary: false },
        ],
      }),
    });
    expect(await screen.findByText("Soy wax")).toBeInTheDocument();
    expect(screen.getByText("Wicks")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Where Wicks went" })).toHaveTextContent("Shelf");
    expect(screen.getByRole("button", { name: "Where Soy wax went" })).toHaveTextContent("Choose a location");
  });

  it("puts away only what has a place chosen", async () => {
    const onClose = renderModal({
      1: stored({}),
      2: stored({
        name: "Wicks",
        unit: "each",
        unassigned: "50",
        placements: [
          { id: 1, location_id: 2, location_path: ["Shelf"], qty: "5", is_primary: true },
          { id: 2, location_id: 1, location_path: ["Loft"], qty: "5", is_primary: false },
        ],
      }),
    });
    fireEvent.click(await screen.findByRole("button", { name: "Put away" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const posts = calls.filter((c) => c.method === "POST" && c.path === "/placements");
    expect(posts).toHaveLength(1);
    // 50 are unplaced, but only 20 just arrived.
    expect(posts[0].body).toMatchObject({ material_id: 2, location_id: 2, qty: "20" });
  });

  it("closes itself when every item already follows its stock", async () => {
    const onClose = renderModal({
      1: stored({ placed: "10", unassigned: "0", follows_stock: true, placements: [{ id: 1, location_id: 2, location_path: ["Shelf"], qty: "10", is_primary: true }] }),
      2: stored({ name: "Wicks", unassigned: "0", total: "0" }),
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("does not trust a cached answer from before the stock arrived", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } });
    // What the page was showing before the receipt: nothing unplaced.
    client.setQueryData(["stored-in", { material_id: 1 }], stored({ total: "0", unassigned: "0" }));
    client.setQueryData(["stored-in", { material_id: 2 }], stored({ total: "0", unassigned: "0" }));
    const onClose = renderModal({ 1: stored({}), 2: stored({ name: "Wicks", unassigned: "0" }) }, vi.fn(), client);
    expect(await screen.findByText("Soy wax")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("says how much just arrived when more than that is unplaced", async () => {
    renderModal({ 1: stored({ unassigned: "60", total: "60" }), 2: stored({ name: "Wicks", unassigned: "0" }) });
    expect(await screen.findByText(/10 kg just added · 60 kg not placed yet/)).toBeInTheDocument();
  });

  it("lets an unplaced item be left alone for good, even on Not now", async () => {
    const onClose = renderModal({ 1: stored({}), 2: stored({ name: "Wicks", unassigned: "0" }) });
    fireEvent.click(await screen.findByLabelText("Don’t ask about Soy wax again"));
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ material_id: 1, ask: false });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("does not ask about an unplaced item that was switched off, and offers no opt-out for a split one", async () => {
    renderModal({
      1: stored({ ask_to_put_away: false }),
      2: stored({
        name: "Wicks",
        unassigned: "50",
        ask_to_put_away: false,
        placements: [
          { id: 1, location_id: 2, location_path: ["Shelf"], qty: "5", is_primary: true },
          { id: 2, location_id: 1, location_path: ["Loft"], qty: "5", is_primary: false },
        ],
      }),
    });
    expect(await screen.findByText("Wicks")).toBeInTheDocument();
    expect(screen.queryByText("Soy wax")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
