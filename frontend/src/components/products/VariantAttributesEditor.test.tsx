import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls, FakeApiError } = await import("../../test/fakeBackend");
const { DirtyRegistryProvider } = await import("../../hooks/useDirtyRegistry");
const { VariantAttributesEditor } = await import("./VariantAttributesEditor");

const PRODUCT = {
  id: 48,
  name: "Yoto Card Stand",
  sku: "SKU-0048",
  variant_attribute1_name: null,
  variant_attribute2_name: null,
  variant_attribute3_name: null,
} as never;

/**
 * What the server answers when two attributes both resolve a combination onto one
 * material — the case the dialog exists for. Shaped like services/variants.generate_variants'
 * 409 detail.
 */
const SHARED = {
  code: "shared_material_variants",
  message: "1 of 2 new variants would use the same material on more than one BOM line.",
  variants: [
    {
      variant_name: "Apple / Apple",
      message:
        "Variant 'Apple / Apple': Apple Green would be used by 2 BOM lines — Primary 'Apple' substituting " +
        "Latte Brown, and Accent 'Apple' substituting Ice Blue.",
    },
  ],
  new_variant_count: 2,
};

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DirtyRegistryProvider>
        <VariantAttributesEditor product={PRODUCT} />
      </DirtyRegistryProvider>
    </QueryClientProvider>
  );
}

let generateResponses: (() => unknown)[];

beforeEach(() => {
  generateResponses = [];
  setRoutes([
    { method: "GET", path: "/products/48/bom", respond: () => [] },
    // No variants yet, so the form opens expanded.
    { method: "GET", path: "/products/48/variants", respond: () => [] },
    { method: "GET", path: "/materials", respond: () => [] },
    { method: "GET", path: "/materials/colours", respond: () => [] },
    { method: "POST", path: "/products/48/variants/generate", respond: () => generateResponses.shift()!() },
  ]);
});

async function fillAndGenerate() {
  renderEditor();
  await userEvent.type(await screen.findByPlaceholderText("Size, Colour…"), "Primary");
  await userEvent.type(screen.getByPlaceholderText("Small, Medium, Large"), "Apple, Ash");
  await userEvent.click(screen.getByRole("button", { name: "Generate variants" }));
}

const generateCalls = () => calls.filter((c) => c.method === "POST" && c.path === "/products/48/variants/generate");

it("asks first, and re-submits with 'keep' when the user keeps the overlapping variants", async () => {
  generateResponses = [
    () => {
      throw new FakeApiError(409, SHARED.message, SHARED);
    },
    () => [],
  ];
  await fillAndGenerate();

  const dialog = await screen.findByRole("dialog", { name: "Same material on two lines" });
  expect(dialog).toHaveTextContent("1 of the 2 new variants");
  expect(dialog).toHaveTextContent("Apple / Apple");
  // The per-variant explanation names the rules, not just the material.
  expect(dialog).toHaveTextContent("Primary 'Apple' substituting Latte Brown");
  // A question, not a failure: the error banner stays empty.
  expect(screen.queryByText(SHARED.message)).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Keep it" }));

  await waitFor(() => expect(generateCalls()).toHaveLength(2));
  const [first, second] = generateCalls().map((c) => c.body as { on_shared_material: string; attributes: unknown });
  expect(first.on_shared_material).toBe("ask");
  expect(second.on_shared_material).toBe("keep");
  // Same rules both times — the answer changes only what's done with the overlap.
  expect(second.attributes).toEqual(first.attributes);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("re-submits with 'skip' when the user leaves them out", async () => {
  generateResponses = [
    () => {
      throw new FakeApiError(409, SHARED.message, SHARED);
    },
    () => [],
  ];
  await fillAndGenerate();

  await screen.findByRole("dialog", { name: "Same material on two lines" });
  await userEvent.click(screen.getByRole("button", { name: "Leave it out" }));

  await waitFor(() => expect(generateCalls()).toHaveLength(2));
  expect((generateCalls()[1].body as { on_shared_material: string }).on_shared_material).toBe("skip");
});

it("cancelling creates nothing and keeps the input", async () => {
  generateResponses = [
    () => {
      throw new FakeApiError(409, SHARED.message, SHARED);
    },
  ];
  await fillAndGenerate();

  await screen.findByRole("dialog", { name: "Same material on two lines" });
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(generateCalls()).toHaveLength(1);
  expect(screen.getByPlaceholderText("Small, Medium, Large")).toHaveValue("Apple, Ash");
});

it("shows any other generate failure as a plain error", async () => {
  generateResponses = [
    () => {
      throw new FakeApiError(400, "Attribute 'Primary' needs at least one value");
    },
  ];
  await fillAndGenerate();

  await screen.findByText("Attribute 'Primary' needs at least one value");
  expect(screen.queryByRole("dialog")).toBeNull();
});
