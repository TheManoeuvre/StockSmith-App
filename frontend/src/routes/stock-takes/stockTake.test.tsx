import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () =>
  (await import("../../test/fakeBackend")).clientMock(),
);
vi.mock("../../lib/tauri", () => ({
  getSettings: () =>
    Promise.resolve({ backendUrl: "http://test", sharedPassword: "pw" }),
  saveSettings: () => Promise.resolve(),
  pickFile: () => Promise.resolve(null),
  saveFileTo: () => Promise.resolve(null),
  restartApp: () => Promise.resolve(),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, setFetchResponder, calls, fetchCalls } =
  await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

function line(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    material_id: 1,
    product_id: null,
    variant_id: null,
    name: "Grey Resin",
    unit: "ml",
    section: "Materials",
    group: "resin",
    subgroup: "",
    expected_qty: "10.0000",
    allocated_qty_at_start: null,
    counted_qty: null,
    notes: null,
    status: "pending",
    system_qty_at_approval: null,
    conflict_reason: null,
    delta: null,
    ...overrides,
  };
}

function take(lines = [line()]) {
  return {
    id: 1,
    status: "open",
    includes_materials: true,
    includes_products: false,
    overdue_only: false,
    scope_description: "All materials",
    started_at: "2026-08-16T10:00:00Z",
    closed_at: null,
    notes: null,
    open_days: 0,
    progress_status: "open",
    line_count: lines.length,
    counted_count: lines.filter((l) => l.counted_qty !== null).length,
    completed_count: lines.filter((l) => l.counted_qty !== null).length,
    pending_count: lines.filter((l) => l.counted_qty === null).length,
    conflict_count: 0,
    lines,
  };
}

function baseRoutes(detail = take()) {
  return [
    { method: "GET" as const, path: "/stock-takes/1", respond: () => detail },
    { method: "GET" as const, path: "/stock-takes", respond: () => [] },
    { method: "GET" as const, path: "/product-categories", respond: () => [] },
    { method: "GET" as const, path: "/dashboard/summary", respond: () => ({}) },
    {
      method: "PUT" as const,
      path: "/stock-takes/1/lines",
      respond: () => detail,
    },
    {
      method: "GET" as const,
      path: "/system/status",
      respond: () => ({ status: "ok" }),
    },
  ];
}

async function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  // Generous timeout: the first render in a file resolves the whole lazy route tree.
  await screen.findByRole(
    "heading",
    { name: /Stock take #1/ },
    { timeout: 5000 },
  );
  return router;
}

beforeEach(() => {
  setRoutes(baseRoutes());
  setFetchResponder(null);
});

describe("count sheet", () => {
  const countInput = () => screen.getByRole("spinbutton");
  const saveButton = () => screen.getByRole("button", { name: "Save counts" });

  it("enables Save only once a count has been typed", async () => {
    const user = userEvent.setup();
    await renderAt("/stock-takes/1");

    expect(saveButton()).toBeDisabled();
    await user.type(countInput(), "8");
    expect(saveButton()).toBeEnabled();
  });

  it("clears a count back to null rather than sending zero", async () => {
    // The whole no-count-means-no-change rule rests on this distinction, and the form holds
    // counts as strings, so an emptied box reaching the server as 0 is a live possibility —
    // it would adjust the item to nothing and date it as counted.
    const user = userEvent.setup();
    setRoutes(
      baseRoutes(take([line({ counted_qty: "8.0000", status: "counted" })])),
    );
    await renderAt("/stock-takes/1");

    await user.clear(countInput());
    await user.click(saveButton());

    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT")).toBe(true),
    );
    const sent = calls.find((c) => c.method === "PUT")!.body as {
      lines: { counted_qty: string | null }[];
    };
    expect(sent.lines[0].counted_qty).toBeNull();
  });

  it("blocks approving while counts are unsaved", async () => {
    const user = userEvent.setup();
    await renderAt("/stock-takes/1");

    await user.type(countInput(), "8");

    expect(
      screen.getByRole("button", { name: "Review and approve" }),
    ).toBeDisabled();
  });

  it("shows how much of the expected figure is already picked for orders", async () => {
    // Without this the shelf looks short and the count reads as a variance.
    setRoutes(
      baseRoutes(
        take([
          line({
            material_id: null,
            product_id: 1,
            name: "Boxed Coaster",
            unit: "each",
            allocated_qty_at_start: "5.0000",
          }),
        ]),
      ),
    );
    await renderAt("/stock-takes/1");

    expect(screen.getByText(/5 picked for orders/)).toBeInTheDocument();
  });
});

describe("CSV import confirmation", () => {
  async function uploadFile(result: Record<string, unknown>) {
    setFetchResponder(() => result);
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    // fireEvent rather than user.upload: the input is deliberately hidden behind a styled
    // button, and userEvent refuses to interact with something it can't see.
    const file = new File(["line_id,counted_qty\n1,8\n"], "sheet.csv", {
      type: "text/csv",
    });
    Object.defineProperty(file, "arrayBuffer", {
      value: () => Promise.resolve(new ArrayBuffer(8)),
    });
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByRole("heading", { name: "Check before applying" });
  }

  it("previews without applying, then applies on confirm", async () => {
    const user = userEvent.setup();
    await renderAt("/stock-takes/1");

    await uploadFile({
      matched: 1,
      skipped_blank: 0,
      failed: [],
      applied: false,
    });

    // The first call must be a dry run — that is what makes the preview a preview.
    expect(fetchCalls[0].url).toContain("dry_run=true");
    expect(screen.getByText(/Nothing has been saved yet/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Apply 1 count$/ }));

    await waitFor(() => expect(fetchCalls).toHaveLength(2));
    expect(fetchCalls[1].url).toContain("dry_run=false");
    expect(fetchCalls[1].url).toContain("on_error=skip");
  });

  it("offers to apply nothing when rows failed, and sends on_error=fail", async () => {
    const user = userEvent.setup();
    await renderAt("/stock-takes/1");

    await uploadFile({
      matched: 1,
      skipped_blank: 0,
      failed: [{ row: 3, error: "'oops' is not a number" }],
      applied: false,
    });

    // The failing row is named, because the point of the screen is knowing what to fix.
    expect(
      screen.getByText(/Row 3: 'oops' is not a number/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Apply nothing/ }));

    await waitFor(() => expect(fetchCalls).toHaveLength(2));
    expect(fetchCalls[1].url).toContain("on_error=fail");
  });

  it("offers no all-or-nothing choice when every row parsed", async () => {
    await renderAt("/stock-takes/1");

    await uploadFile({
      matched: 2,
      skipped_blank: 0,
      failed: [],
      applied: false,
    });

    expect(
      screen.queryByRole("button", { name: /Apply nothing/ }),
    ).not.toBeInTheDocument();
  });

  it("says blank rows are left alone rather than counted as zero", async () => {
    await renderAt("/stock-takes/1");

    await uploadFile({
      matched: 1,
      skipped_blank: 2,
      failed: [],
      applied: false,
    });

    expect(
      screen.getByText(/aren't treated as a count of zero/),
    ).toBeInTheDocument();
  });

  it("cancelling applies nothing", async () => {
    const user = userEvent.setup();
    await renderAt("/stock-takes/1");

    await uploadFile({
      matched: 1,
      skipped_blank: 0,
      failed: [],
      applied: false,
    });
    const dialog = within(
      screen
        .getByRole("heading", { name: "Check before applying" })
        .closest("div")!.parentElement!,
    );
    await user.click(dialog.getByRole("button", { name: "Cancel" }));

    expect(fetchCalls).toHaveLength(1);
  });
});

describe("grouping", () => {
  it("puts a heading above each group, and hides its rows when collapsed", async () => {
    const user = userEvent.setup();
    setRoutes([
      ...baseRoutes().filter((r) => r.path !== "/stock-takes/1"),
      {
        method: "GET" as const,
        path: "/stock-takes/1",
        respond: () =>
          take([
            line({
              id: 1,
              material_id: null,
              product_id: 5,
              name: "Slate Coaster — Round",
              unit: "each",
              section: "Products",
              group: "Coaster",
              subgroup: "COA-1",
            }),
            line({
              id: 2,
              material_id: null,
              product_id: 5,
              name: "Slate Coaster — Square",
              unit: "each",
              section: "Products",
              group: "Coaster",
              subgroup: "COA-1",
            }),
            line({
              id: 3,
              name: "Grey Resin",
              section: "Materials",
              group: "resin",
              subgroup: "",
            }),
          ]),
      },
    ]);

    await renderAt("/stock-takes/1");

    // The parent SKU is what a coaster's variants group under; a material with no type
    // says so rather than showing an empty heading.
    expect(screen.getByText("Coaster · COA-1")).toBeInTheDocument();
    expect(screen.getByText("resin")).toBeInTheDocument();
    expect(screen.getByText("counted 0 of 2")).toBeInTheDocument();
    expect(screen.getByText("Slate Coaster — Round")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Coaster · COA-1/ }));

    // Collapsing is what makes a two-hundred-line sheet workable one shelf at a time.
    expect(screen.queryByText("Slate Coaster — Round")).not.toBeInTheDocument();
    expect(screen.getByText("Grey Resin")).toBeInTheDocument();
  });
});
