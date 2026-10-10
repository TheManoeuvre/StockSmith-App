import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes } = await import("../../test/fakeBackend");
const { DuplicateColoursNotice } = await import("./DuplicateColoursNotice");

const petg = { material_type_id: 3, material_type_name: "PETG", is_active: true, unit: "g", current_qty: "500" };
const SUNLU = { id: 9, name: "Sunlu | PETG | Matte White", colour: "White", ...petg };
const BAMBU = { id: 56, name: "Bambu Lab | PETG Basic | White", colour: "White", ...petg };
const BLACK = { id: 17, name: "Bambu Lab | PETG HF | Black", colour: "Black", ...petg };

function renderNotice(materials: unknown[]) {
  setRoutes([{ method: "GET", path: "/materials", respond: () => materials }]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DuplicateColoursNotice materials={materials as never} />
    </QueryClientProvider>,
  );
}

it("shows nothing when no colour is repeated", () => {
  const { container } = renderNotice([SUNLU, BLACK]);
  expect(container).toBeEmptyDOMElement();
});

it("is collapsed until opened, then offers to merge the newer into the older", async () => {
  renderNotice([SUNLU, BLACK, BAMBU]);

  expect(screen.getByText(/1 colour appears on more than one material/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Merge/ })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: /1 colour appears/ }));
  // The oldest is kept as the target, so only the newer carries a button.
  expect(screen.getAllByRole("button", { name: /^Merge / })).toHaveLength(1);

  await userEvent.click(screen.getByRole("button", { name: `Merge ${BAMBU.name} into ${SUNLU.name}` }));
  expect(await screen.findByRole("dialog")).toBeTruthy();
});
