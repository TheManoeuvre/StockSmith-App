import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes } = await import("../../test/fakeBackend");
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
