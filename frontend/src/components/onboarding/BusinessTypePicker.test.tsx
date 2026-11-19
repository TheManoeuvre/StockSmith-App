import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const routerState = { pathname: "/" };
const navigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { pathname: routerState.pathname } }),
  useNavigate: () => navigate,
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { BusinessTypePicker } = await import("./BusinessTypePicker");

const presets = [
  {
    key: "candles_soap",
    label: "Candles, soap & bath",
    description: "Candles, wax melts, soap, bath bombs and skincare.",
    categories: ["wax", "fragrance", "packaging"],
    units: ["each", "g", "ml"],
    material_types: ["Soy wax"],
  },
  {
    key: "general",
    label: "Something else",
    description: "A simple starting set.",
    categories: ["raw materials", "packaging"],
    units: ["each", "g"],
    material_types: [],
  },
];

function serve(state: { business_type: string | null; can_choose: boolean }) {
  setRoutes([
    { method: "GET", path: "/settings/business-type", respond: () => ({ ...state, presets }) },
    {
      method: "PUT",
      path: "/settings/business-type",
      respond: (body) => ({ business_type: (body as { business_type: string }).business_type, can_choose: false, presets }),
    },
  ]);
}

function renderPicker() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BusinessTypePicker />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  routerState.pathname = "/";
  navigate.mockReset();
});

describe("BusinessTypePicker", () => {
  it("asks a fresh install what it makes, previews the choice, and applies it", async () => {
    serve({ business_type: null, can_choose: true });
    renderPicker();

    expect(await screen.findByRole("dialog", { name: "Welcome to StockSmith" })).toBeInTheDocument();
    const setUp = screen.getByRole("button", { name: "Set up StockSmith" });
    expect(setUp).toBeDisabled();

    fireEvent.click(screen.getByRole("radio", { name: /Candles, soap & bath/ }));
    expect(screen.getByText("Material categories:")).toBeInTheDocument();
    expect(screen.getByText("Soy wax")).toBeInTheDocument();

    fireEvent.click(setUp);
    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "PUT",
        path: "/settings/business-type",
        body: { business_type: "candles_soap" },
      }),
    );
  });

  it("hides the example-types row for a preset that has none", async () => {
    serve({ business_type: null, can_choose: true });
    renderPicker();
    fireEvent.click(await screen.findByRole("radio", { name: /Something else/ }));
    expect(screen.queryByText("Example material types:")).not.toBeInTheDocument();
  });

  it("never shows once a business type is chosen", async () => {
    serve({ business_type: "3d_printing", can_choose: false });
    renderPicker();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stands down on Settings, where restoring a backup happens", async () => {
    routerState.pathname = "/settings";
    serve({ business_type: null, can_choose: true });
    renderPicker();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers restoring a backup instead", async () => {
    serve({ business_type: null, can_choose: true });
    renderPicker();
    fireEvent.click(await screen.findByRole("button", { name: /Restore a backup instead/ }));
    expect(navigate).toHaveBeenCalledWith({ to: "/settings", search: { page: "backup-restore" } });
  });
});
