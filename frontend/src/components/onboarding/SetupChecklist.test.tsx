import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getHelpPage, headingAnchors } from "../../lib/help";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, search, hash }: { children: ReactNode; to: string; search?: { page?: string }; hash?: string }) => (
    <a href={`${to}${search?.page ? `?page=${search.page}` : ""}${hash ? `#${hash}` : ""}`}>{children}</a>
  ),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { SetupChecklist, ShowSetupChecklist, STEP_COPY } = await import("./SetupChecklist");

const KEYS = ["business_type", "materials", "opening_stock", "kitting", "products", "orders", "offsite_backup"] as const;
const OPTIONAL = new Set(["kitting", "orders", "offsite_backup"]);

function status(doneUpTo: number, extra: { dismissed?: boolean } = {}) {
  const steps = KEYS.map((key, i) => ({ key, done: i < doneUpTo, optional: OPTIONAL.has(key) }));
  return { steps, dismissed: extra.dismissed ?? false, complete: steps.every((s) => s.done) };
}

function serve(initial: ReturnType<typeof status>) {
  let current = initial;
  setRoutes([
    { method: "GET", path: "/onboarding/status", respond: () => current },
    {
      method: "PUT",
      path: "/onboarding/checklist",
      respond: (body) => (current = { ...current, dismissed: (body as { dismissed: boolean }).dismissed }),
    },
  ]);
}

function renderWith(node: ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>,
  );
}

beforeEach(() => setRoutes([]));

describe("SetupChecklist", () => {
  it("shows progress and points only at the next step", async () => {
    serve(status(2));
    renderWith(<SetupChecklist />);

    const region = await screen.findByRole("region", { name: "Set up StockSmith" });
    expect(within(region).getByText(/2 of 7 done/)).toBeInTheDocument();
    expect(within(region).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");

    // The next step is opening stock: it alone gets a call to action.
    const action = within(region).getByRole("link", { name: "Start a stock take" });
    expect(action).toHaveAttribute("href", "/stock-takes");
    expect(within(region).queryByRole("link", { name: "Go to Products" })).not.toBeInTheDocument();
    expect(within(region).getAllByText("Optional")).toHaveLength(3);
  });

  it("hides when dismissed, and when everything is done", async () => {
    serve(status(2, { dismissed: true }));
    renderWith(<SetupChecklist />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("hides when every step is done", async () => {
    serve(status(7));
    renderWith(<SetupChecklist />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("can be hidden, and brought back from Help", async () => {
    serve(status(1));
    renderWith(
      <>
        <SetupChecklist />
        <ShowSetupChecklist />
      </>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Hide checklist" }));
    await waitFor(() => expect(screen.queryByRole("region")).not.toBeInTheDocument());

    fireEvent.click(await screen.findByRole("button", { name: "Show it on the Dashboard" }));
    expect(await screen.findByRole("region", { name: "Set up StockSmith" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ dismissed: true }, { dismissed: false }]);
  });

  it("stays out of the way if the status can't be loaded", async () => {
    renderWith(<SetupChecklist />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});

describe("STEP_COPY", () => {
  it.each(Object.entries(STEP_COPY))("%s links to a help heading that exists", (_key, copy) => {
    const page = getHelpPage(copy.help.doc);
    expect(page).not.toBeNull();
    if (copy.help.anchor) expect(headingAnchors(page!.body)).toContain(copy.help.anchor);
  });
});
