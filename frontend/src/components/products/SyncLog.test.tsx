import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listingPushLog = vi.fn();

vi.mock("../../api/platforms", () => ({
  platformsApi: {
    listingPushLog: (...args: unknown[]) => listingPushLog(...args),
  },
}));

const { SyncLog } = await import("./SyncLog");

function renderLog(productId = 7) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SyncLog productId={productId} platform="etsy" />
    </QueryClientProvider>,
  );
}

function row(id: number, productId: number, over: Record<string, unknown> = {}) {
  return {
    id,
    product_id: productId,
    product_name: "Widget",
    variant_id: null,
    variant_name: "Red",
    platform: "etsy",
    attempted_qty: 22,
    status: "success",
    error_message: null,
    attempted_at: "2026-08-20T09:12:00Z",
    ...over,
  };
}

describe("SyncLog", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists only this product's push rows", async () => {
    listingPushLog.mockResolvedValue({
      items: [row(1, 7), row(2, 99, { variant_name: "Other product" }), row(3, 7, { status: "error" })],
      total: 3,
    });
    renderLog(7);

    expect(await screen.findByText("Sync log")).toBeInTheDocument();
    expect(await screen.findAllByText(/Red · qty 22/)).toHaveLength(2);
    expect(screen.queryByText(/Other product/)).not.toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("renders nothing when the product has no push history", async () => {
    listingPushLog.mockResolvedValue({ items: [row(1, 99)], total: 1 });
    const { container } = renderLog(7);
    // Wait a tick for the query to settle, then assert the component stayed empty.
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
