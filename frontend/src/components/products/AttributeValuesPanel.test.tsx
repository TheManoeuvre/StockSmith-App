import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls, FakeApiError } = await import("../../test/fakeBackend");
const { AttributeValuesPanel } = await import("./AttributeValuesPanel");

const PRODUCT = {
  id: 7,
  name: "Brick Pencil Pot",
  sku: "BPP",
  variant_attribute1_name: "Size",
  variant_attribute2_name: "Colour",
  variant_attribute3_name: null,
} as never;

const variant = (id: number, size: string, colour: string, active = true) => ({
  id,
  product_id: 7,
  variant_name: `${size} / ${colour}`,
  sku_suffix: null,
  full_sku: null,
  is_active: active,
  current_stock: 0,
  allocated_qty: 0,
  attribute1_value: size,
  attribute2_value: colour,
  attribute3_value: null,
});

const VARIANTS = [
  variant(1, "4 Stud", "Red"),
  variant(2, "4 Stud Standard", "Red"),
  // Disabled — its value must still be listed, or it could never be renamed away.
  variant(3, "6 Stud Standard", "Red", false),
  variant(4, "4 Stud", "Blue"),
];

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AttributeValuesPanel product={PRODUCT} />
    </QueryClientProvider>
  );
}

let renameResponse: () => unknown;

beforeEach(() => {
  renameResponse = () => ({ variants_updated: 1, live_platforms: [] });
  setRoutes([
    { method: "GET", path: "/products/7/variants", respond: () => VARIANTS },
    { method: "POST", path: "/products/7/attribute-values/rename", respond: () => renameResponse() },
    { method: "PATCH", path: "/products/7", respond: (body) => ({ ...(PRODUCT as object), ...(body as object) }) },
    {
      method: "POST",
      path: "/products/7/attribute-values/merge/preview",
      respond: () => ({ pairs: [], relabel_only: [], bom_differs: false, kitting_differs: false, blockers: [] }),
    },
  ]);
});

it("renames an attribute through the product PATCH, naming only that slot", async () => {
  const user = userEvent.setup();
  renderPanel();

  await user.click(await screen.findByRole("button", { name: "Rename attribute Size" }));
  const input = screen.getByLabelText("New name for attribute Size");
  await user.clear(input);
  await user.type(input, "Stud size{Enter}");

  await waitFor(() =>
    expect(calls.find((c) => c.method === "PATCH")).toEqual({
      method: "PATCH",
      path: "/products/7",
      body: { variant_attribute1_name: "Stud size" },
    })
  );
});

it("lists each attribute's distinct values, including those only on disabled variants", async () => {
  renderPanel();

  expect(await screen.findByText("Size")).toBeInTheDocument();
  expect(screen.getByText("4 Stud")).toBeInTheDocument();
  expect(screen.getByText("4 Stud Standard")).toBeInTheDocument();
  expect(screen.getByText("6 Stud Standard")).toBeInTheDocument();
  expect(screen.getByText("Colour")).toBeInTheDocument();
  // One badge per distinct value, not one per variant.
  expect(screen.getAllByText("Red")).toHaveLength(1);
});

it("renames a value with the slot it belongs to", async () => {
  const user = userEvent.setup();
  renderPanel();

  await user.click(await screen.findByRole("button", { name: "Rename 4 Stud Standard" }));
  const input = screen.getByLabelText("New name for 4 Stud Standard");
  await user.clear(input);
  await user.type(input, "4 Stud Std{Enter}");

  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      slot: 1,
      old_value: "4 Stud Standard",
      new_value: "4 Stud Std",
    })
  );
});

it("warns when live listings will keep the old label until pushed", async () => {
  renameResponse = () => ({ variants_updated: 2, live_platforms: ["etsy"] });
  const user = userEvent.setup();
  renderPanel();

  await user.click(await screen.findByRole("button", { name: "Rename 4 Stud Standard" }));
  await user.type(screen.getByLabelText("New name for 4 Stud Standard"), " X{Enter}");

  expect(await screen.findByText(/etsy still show the old name/)).toBeInTheDocument();
});

it("explains a 409 as a merge rather than a rename", async () => {
  renameResponse = () => {
    throw new FakeApiError(409, '"4 Stud" is already a value of this attribute.');
  };
  const user = userEvent.setup();
  renderPanel();

  await user.click(await screen.findByRole("button", { name: "Rename 4 Stud Standard" }));
  const input = screen.getByLabelText("New name for 4 Stud Standard");
  await user.clear(input);
  await user.type(input, "4 Stud{Enter}");

  expect(await screen.findByText(/is already a value of this attribute/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: 'Merge "4 Stud Standard" into it' }));

  // The merge modal opens with the clashing value already chosen as the survivor.
  await waitFor(() =>
    expect(calls.find((c) => c.path === "/products/7/attribute-values/merge/preview")?.body).toEqual({
      slot: 1,
      loser_value: "4 Stud Standard",
      survivor_value: "4 Stud",
    })
  );
});

it("opens the merge modal from a value's own button", async () => {
  const user = userEvent.setup();
  renderPanel();

  await user.click(await screen.findByRole("button", { name: "Merge 4 Stud Standard into another value" }));

  expect(await screen.findByRole("dialog", { name: 'Merge Size "4 Stud Standard" into…' })).toBeInTheDocument();
  // Only the other Size values are offered.
  const options = screen.getAllByRole("option").map((o) => o.textContent);
  expect(options).toEqual(["Choose…", "4 Stud", "6 Stud Standard"]);
});

it("renders nothing for a product without attributes", async () => {
  setRoutes([{ method: "GET", path: "/products/7/variants", respond: () => [] }]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}>
      <AttributeValuesPanel
        product={{ ...(PRODUCT as object), variant_attribute1_name: null, variant_attribute2_name: null } as never}
      />
    </QueryClientProvider>
  );
  await waitFor(() => expect(calls.length).toBe(1));
  expect(container).toBeEmptyDOMElement();
});
