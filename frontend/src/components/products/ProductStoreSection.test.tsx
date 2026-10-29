import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Product } from "../../api/types";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { ProductStoreSection } = await import("./ProductStoreSection");

function product(storeStates: Product["store_states"]): Product {
  return { id: 37, store_states: storeStates } as Product;
}

function renderSection(p: Product) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProductStoreSection product={p} platform="ebay">
        <p>eBay panels</p>
      </ProductStoreSection>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  calls.length = 0;
  setRoutes([
    {
      method: "PUT",
      path: "/platforms/ebay/products/37/store",
      respond: () => ({ platform: "ebay", is_on: false, is_locked: false }),
    },
  ]);
});

it("shows the store's panels while the product is on", () => {
  renderSection(product([{ platform: "ebay", is_on: true, is_locked: false }]));
  expect(screen.getByText("eBay panels")).toBeInTheDocument();
});

it("collapses to a one-line note when the product is off", () => {
  renderSection(product([{ platform: "ebay", is_on: false, is_locked: false }]));
  expect(screen.queryByText("eBay panels")).not.toBeInTheDocument();
  expect(screen.getByText(/eBay setup is hidden/)).toBeInTheDocument();
  expect(screen.getByText("Not sold on eBay")).toBeInTheDocument();
});

it("treats a product with no recorded state as on", () => {
  renderSection(product([]));
  expect(screen.getByText("eBay panels")).toBeInTheDocument();
});

it("turns the store off through the store endpoint", async () => {
  renderSection(product([{ platform: "ebay", is_on: true, is_locked: false }]));
  await userEvent.click(screen.getByRole("checkbox", { name: "Sold on eBay" }));
  await waitFor(() =>
    expect(calls).toContainEqual(expect.objectContaining({ method: "PUT", path: "/platforms/ebay/products/37/store", body: { on: false } }))
  );
});

it("pins the switch on while a live listing exists", () => {
  renderSection(product([{ platform: "ebay", is_on: true, is_locked: true }]));
  expect(screen.getByRole("checkbox", { name: "Sold on eBay" })).toBeDisabled();
  expect(screen.getByText(/end the listing/)).toBeInTheDocument();
});
