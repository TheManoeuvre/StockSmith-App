import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StoredIn } from "../../api/locations";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const { setRoutes } = await import("../../test/fakeBackend");
const { TakingFromSelect } = await import("./TakingFromSelect");

const two: StoredIn = {
  name: "Wax",
  unit: "kg",
  total: "10",
  placed: "10",
  unassigned: "0",
  drift: "0",
  follows_stock: false,
  placements: [
    { id: 1, location_id: 2, location_path: ["Shelf"], qty: "4", is_primary: true },
    { id: 2, location_id: 3, location_path: ["Loft"], qty: "6", is_primary: false },
  ],
};

function show(stored: StoredIn, decreasing: boolean, onChange = vi.fn()) {
  setRoutes([{ method: "GET", path: /^\/placements\/stored-in/, respond: () => stored }]);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TakingFromSelect item={{ material_id: 1 }} decreasing={decreasing} value={null} onChange={onChange} />
    </QueryClientProvider>,
  );
  return onChange;
}

beforeEach(() => setRoutes([]));

describe("TakingFromSelect", () => {
  it("offers each place for a decrease on an item kept in several", async () => {
    const onChange = show(two, true);
    const select = await screen.findByLabelText("Taking from");
    expect(screen.getByRole("option", { name: "Primary place first" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Loft (6 kg)" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "3" } });
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("stays out of the way for an increase", async () => {
    show(two, false);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByLabelText("Taking from")).not.toBeInTheDocument();
  });

  it("stays out of the way for an item in one place", async () => {
    show({ ...two, placements: [two.placements[0]] }, true);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByLabelText("Taking from")).not.toBeInTheDocument();
  });
});
