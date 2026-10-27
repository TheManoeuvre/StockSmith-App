import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listingPushLog = vi.fn();

vi.mock("../../../api/platforms", () => ({
  platformsApi: {
    listingPushLog: (...args: unknown[]) => listingPushLog(...args),
    checkAllListings: vi.fn(),
  },
}));

const { StockPushesCard } = await import("./StockPushesCard");

function renderCard(failingPushCount: number) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StockPushesCard platform="ebay" summary={undefined} failingPushCount={failingPushCount} blockedPushCount={0} />
    </QueryClientProvider>,
  );
}

function row(id: number, variantId: number, over: Record<string, unknown> = {}) {
  return {
    id,
    product_id: 23,
    product_name: "Wall Mount Plate",
    variant_id: variantId,
    variant_name: `Variant ${variantId}`,
    platform: "ebay",
    attempted_qty: 20,
    status: "error",
    error_message: `Rejected ${id}`,
    attempted_at: "2026-09-30T07:12:00Z",
    ...over,
  };
}

describe("StockPushesCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists each failing listing once, with its latest error", async () => {
    // Newest first, as the API returns it: variant 1 retried hourly, variant 2 failed once
    // and has since been pushed successfully.
    listingPushLog.mockResolvedValue({
      items: [row(5, 1), row(4, 2, { status: "success", error_message: null }), row(3, 1), row(2, 2), row(1, 1)],
      total: 5,
    });
    renderCard(1);

    expect(await screen.findByText(/Variant 1: Rejected 5/)).toBeInTheDocument();
    expect(screen.queryByText(/Rejected 3/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Variant 2/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});
