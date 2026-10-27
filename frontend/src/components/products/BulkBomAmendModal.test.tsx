import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { BulkBomAmendModal } = await import("./BulkBomAmendModal");

const PRODUCT = {
  id: 37,
  name: "Brick Pencil Pot",
  sku: "SKU-0037",
  variant_attribute1_name: "Studs",
  variant_attribute2_name: "Colour",
  variant_attribute3_name: null,
} as never;

const VARIANTS = [
  { id: 1, is_active: true, attribute1_value: "4 Stud", attribute2_value: "Sunflower Yellow" },
  { id: 2, is_active: true, attribute1_value: "6 Stud", attribute2_value: "Sunflower Yellow" },
  { id: 3, is_active: true, attribute1_value: "6 Stud", attribute2_value: "Teal" },
  // Inactive variants aren't amendable, so their values shouldn't be offered.
  { id: 4, is_active: false, attribute1_value: "8 Stud", attribute2_value: "Retired" },
];

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BulkBomAmendModal product={PRODUCT} onClose={() => {}} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  setRoutes([
    { method: "GET", path: "/products/37/bom", respond: () => [] },
    { method: "GET", path: "/products/37/variants", respond: () => VARIANTS },
    { method: "GET", path: "/materials", respond: () => [] },
  ]);
});

function valueSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: /Value/i }) as HTMLSelectElement;
}

it("offers only the values actually present on active variants", async () => {
  renderModal();
  await waitFor(() => expect(valueSelect().options.length).toBeGreaterThan(1));

  const options = [...valueSelect().options].map((o) => o.textContent);
  // Deduplicated: "6 Stud" is on two variants but is one choice.
  expect(options).toEqual(["Select a value…", "4 Stud", "6 Stud"]);
  expect(options).not.toContain("8 Stud");
});

it("switches the offered values when the attribute changes", async () => {
  renderModal();
  await waitFor(() => expect(valueSelect().options.length).toBeGreaterThan(1));

  await userEvent.selectOptions(screen.getByRole("combobox", { name: /Attribute/i }), "Colour");

  await waitFor(() => {
    const options = [...valueSelect().options].map((o) => o.textContent);
    expect(options).toEqual(["Select a value…", "Sunflower Yellow", "Teal"]);
  });
});

it("clears a chosen value when the attribute changes", async () => {
  // A value carried over from the previous attribute matches no variants, so the preview
  // would come back empty — indistinguishable from "nothing uses this value".
  renderModal();
  await waitFor(() => expect(valueSelect().options.length).toBeGreaterThan(1));

  await userEvent.selectOptions(valueSelect(), "6 Stud");
  expect(valueSelect().value).toBe("6 Stud");

  await userEvent.selectOptions(screen.getByRole("combobox", { name: /Attribute/i }), "Colour");
  await waitFor(() => expect(valueSelect().value).toBe(""));
});

it("leaves hand-set lines alone unless asked, and says which it left", async () => {
  setRoutes([
    { method: "GET", path: "/products/37/bom", respond: () => [{ id: 1, product_id: 37, material_id: 5, qty_required: "10" }] },
    { method: "GET", path: "/products/37/variants", respond: () => VARIANTS },
    { method: "GET", path: "/materials", respond: () => [{ id: 5, name: "Filament", material_type_id: 1, is_active: true }] },
    {
      method: "POST",
      path: "/products/37/variants/bom-overrides/amend",
      respond: () => ({
        applied: false,
        matched_variant_count: 2,
        changed_variant_count: 1,
        skipped_inactive_count: 0,
        kept_manual_count: 1,
        units: [
          { variant_id: 1, variant_name: "4 Stud / Teal", changes: [], kept_manual: [{ base_material_id: 5, base_material_name: "Filament", before_material_id: 5, before_qty: "12", after_material_id: 5, after_qty: "14" }] },
          { variant_id: 2, variant_name: "6 Stud / Teal", changes: [{ base_material_id: 5, base_material_name: "Filament", before_material_id: null, before_qty: null, after_material_id: 5, after_qty: "14" }], kept_manual: [] },
        ],
      }),
    },
  ]);
  renderModal();
  await waitFor(() => expect(valueSelect().options.length).toBeGreaterThan(1));
  await userEvent.selectOptions(valueSelect(), "6 Stud");
  await userEvent.click((await screen.findAllByRole("checkbox"))[0]);
  await userEvent.type(screen.getByPlaceholderText("keep (base 10)"), "14");
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));

  expect(await screen.findByText(/set by hand were left as they are/)).toBeTruthy();
  const amend = () => calls.filter((c) => c.path === "/products/37/variants/bom-overrides/amend");
  expect((amend()[0].body as { include_manual: boolean }).include_manual).toBe(false);

  await userEvent.click(screen.getByRole("checkbox", { name: /overwrite lines set by hand/ }));
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() => expect(amend()).toHaveLength(2));
  expect((amend()[1].body as { include_manual: boolean }).include_manual).toBe(true);
});
