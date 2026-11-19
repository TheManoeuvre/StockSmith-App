import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
const restartApp = vi.fn(() => Promise.resolve());
vi.mock("../../lib/tauri", () => ({ restartApp }));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { ExploreSampleButton, SampleWorkspaceBanner } = await import("./SampleWorkspace");

function serve(mode: "real" | "sample", can_switch = true) {
  setRoutes([
    { method: "GET", path: "/workspace", respond: () => ({ mode, can_switch, sample_business: "Ember & Fig Candle Co. (sample)" }) },
    { method: "PUT", path: "/workspace", respond: (body) => ({ ...(body as object), can_switch, sample_business: "x" }) },
  ]);
}

function renderWith(node: ReactNode) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

beforeEach(() => restartApp.mockClear());

describe("SampleWorkspaceBanner", () => {
  it("says plainly that the data isn't real, and leaves via a restart", async () => {
    serve("sample");
    renderWith(<SampleWorkspaceBanner />);
    expect(await screen.findByText(/Nothing here is real/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Leave sample data" }));
    fireEvent.click(await screen.findByRole("button", { name: "Leave and restart" }));
    await waitFor(() => expect(restartApp).toHaveBeenCalled());
    expect(calls).toContainEqual({ method: "PUT", path: "/workspace", body: { mode: "real" } });
  });

  it("isn't shown on real data", async () => {
    serve("real");
    renderWith(<SampleWorkspaceBanner />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByText(/Sample data:/)).not.toBeInTheDocument();
  });
});

describe("ExploreSampleButton", () => {
  it("opens the sample with a restart", async () => {
    serve("real");
    renderWith(<ExploreSampleButton />);
    fireEvent.click(await screen.findByRole("button", { name: "Look around with sample data first" }));
    await waitFor(() => expect(restartApp).toHaveBeenCalled());
    expect(calls).toContainEqual({ method: "PUT", path: "/workspace", body: { mode: "sample" } });
  });

  it("is hidden where switching isn't possible, and inside the sample itself", async () => {
    serve("real", false);
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <ExploreSampleButton />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    unmount();

    serve("sample");
    renderWith(<ExploreSampleButton />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
