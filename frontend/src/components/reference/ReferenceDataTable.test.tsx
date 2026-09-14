import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

// Only useBlocker is stubbed, and only because it needs a live router. Everything the table
// actually drives — the registry, dirty tracking, guard.attempt() and the dialog — stays real,
// since the interaction between them is what these tests are about. Router-level blocking is
// covered where it belongs, in settings.test.tsx.
vi.mock("@tanstack/react-router", () => ({
  useBlocker: () => ({ status: "idle", proceed: () => {}, reset: () => {} }),
}));

const { setRoutes, calls, FakeApiError } = await import("../../test/fakeBackend");
const { manufacturersApi } = await import("../../api/manufacturers");
const { materialCategoriesApi } = await import("../../api/materialCategories");
const { ReferenceDataTable } = await import("./ReferenceDataTable");
const { DirtyRegistryProvider } = await import("../../hooks/useDirtyRegistry");
const { GuardProvider, useUnsavedChangesGuard } = await import("../../hooks/useUnsavedChangesGuard");
const { UnsavedChangesDialog } = await import("../common/UnsavedChangesDialog");

const ROWS = [
  { id: 1, name: "Bambu Lab", website_url: null, created_at: "2026-01-01T00:00:00Z", usage_count: 3 },
  { id: 2, name: "Prusa", website_url: "https://prusa3d.com", created_at: "2026-01-01T00:00:00Z", usage_count: 0 },
];

function routes(rows = ROWS, overrides: Record<string, unknown> = {}) {
  return [
    { method: "GET" as const, path: "/manufacturers", respond: () => rows },
    {
      method: "PATCH" as const,
      path: /^\/manufacturers\/\d+$/,
      respond: overrides.patch ?? ((body: unknown) => ({ ...rows[0], ...(body as object) })),
    },
    { method: "DELETE" as const, path: /^\/manufacturers\/\d+$/, respond: () => null },
    { method: "POST" as const, path: /^\/manufacturers\/\d+\/merge$/, respond: () => rows[0] },
    { method: "POST" as const, path: "/manufacturers/find-or-create", respond: (body: unknown) => body },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ] as never[];
}

/**
 * Mounted with the real registry, guard and dialog, exactly as __root.tsx wires them — the point
 * of most of these tests is the interaction between the table and that machinery, so stubbing it
 * would test nothing.
 */
function Harness() {
  const guard = useUnsavedChangesGuard();
  return (
    <GuardProvider guard={guard}>
      <ReferenceDataTable
        title="Manufacturers"
        segment="manufacturers"
        queryKey={["manufacturers"]}
        api={{
          list: manufacturersApi.list,
          create: manufacturersApi.findOrCreate,
          update: manufacturersApi.update,
          remove: manufacturersApi.remove,
          merge: manufacturersApi.merge,
        }}
        fields={[
          { key: "name", label: "Name" },
          { key: "website_url", label: "Website", type: "url" },
        ]}
        usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
      />
      <UnsavedChangesDialog {...guard.dialogProps} />
    </GuardProvider>
  );
}

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DirtyRegistryProvider>
        <Harness />
      </DirtyRegistryProvider>
    </QueryClientProvider>
  );
}

const row = (name: string) => screen.getByRole("button", { name: new RegExp(name) });

describe("ReferenceDataTable", () => {
  beforeEach(() => setRoutes(routes()));

  describe("expand in place", () => {
    it("opens a row without navigating", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Bambu Lab/ }));

      expect(screen.getByLabelText("Bambu Lab Name")).toHaveValue("Bambu Lab");
    });

    it("shows usage at a glance so you can see what's safe to remove", async () => {
      renderTable();

      expect(await screen.findByText("3 materials")).toBeInTheDocument();
      expect(screen.getByText("unused")).toBeInTheDocument();
    });

    it("switching between entries costs one click, not a page load", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Bambu Lab/ }));
      await user.click(row("Prusa"));

      expect(screen.queryByLabelText("Bambu Lab Name")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Prusa Name")).toHaveValue("Prusa");
    });
  });

  describe("editing", () => {
    it("enables Save only once something changed, then sends the edit", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      const save = screen.getByRole("button", { name: "Save" });
      expect(save).toBeDisabled();

      const nameField = screen.getByLabelText("Prusa Name");
      await user.clear(nameField);
      await user.type(nameField, "Prusa Research");
      expect(save).toBeEnabled();

      await user.click(save);

      await waitFor(() => {
        const patch = calls.find((c) => c.method === "PATCH");
        expect(patch?.body).toMatchObject({ name: "Prusa Research" });
      });
    });

    it("warns before collapsing a row with unsaved edits", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      await user.type(screen.getByLabelText("Prusa Name"), " Research");

      await user.click(row("Prusa"));

      // Collapsing unmounts the editor, and an unmount can't be cancelled after the fact — so
      // the guard has to be asked before the state change, not after.
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(screen.getByLabelText("Prusa Name")).toBeInTheDocument();
    });

    it("warns before switching to a different row with unsaved edits", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      await user.type(screen.getByLabelText("Prusa Name"), "!");

      await user.click(row("Bambu Lab"));

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("lets the switch through after discarding", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      await user.type(screen.getByLabelText("Prusa Name"), "!");
      await user.click(row("Bambu Lab"));
      await user.click(await screen.findByRole("button", { name: "Discard changes" }));

      expect(await screen.findByLabelText("Bambu Lab Name")).toBeInTheDocument();
    });
  });

  describe("deleting", () => {
    it("refuses an entry that is still in use, and says what uses it", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Bambu Lab/ }));

      // Disabled rather than allowed-then-rejected: the FKs are ON DELETE SET NULL, so a delete
      // that got through would blank the manufacturer on three materials and look like success.
      const button = screen.getByRole("button", { name: /In use by 3 materials/ });
      expect(button).toBeDisabled();
    });

    it("deletes an unused entry after confirming", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      await user.click(screen.getByRole("button", { name: "Delete" }));

      const dialog = within(await screen.findByRole("dialog"));
      // No typed gate here — that friction is reserved for restore, so it keeps its meaning.
      expect(dialog.queryByRole("textbox")).toBeNull();
      await user.click(dialog.getByRole("button", { name: "Delete" }));

      await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    });
  });

  describe("merging", () => {
    it("offers a merge when a rename collides", async () => {
      const user = userEvent.setup();
      setRoutes(
        routes(ROWS, {
          patch: () => {
            throw new FakeApiError(409, 'Another entry is already called "Bambu Lab".');
          },
        })
      );
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      const nameField = screen.getByLabelText("Prusa Name");
      await user.clear(nameField);
      await user.type(nameField, "Bambu Lab");
      await user.click(screen.getByRole("button", { name: "Save" }));

      // A dead end ("that name is taken") would leave the duplicate in place. The list is
      // already loaded, so the clashing row is found locally — no structured error needed.
      expect(await screen.findByRole("button", { name: "Merge into it" })).toBeInTheDocument();
    });

    it("merges into a chosen entry after confirming", async () => {
      const user = userEvent.setup();
      renderTable();

      await user.click(await screen.findByRole("button", { name: /Prusa/ }));
      await user.selectOptions(screen.getByLabelText("Merge into"), "1");
      await user.click(screen.getByRole("button", { name: "Merge" }));

      const dialog = within(await screen.findByRole("dialog"));
      expect(dialog.getByText(/will be deleted/)).toBeInTheDocument();
      await user.click(dialog.getByRole("button", { name: "Merge" }));

      await waitFor(() => {
        const merge = calls.find((c) => c.path === "/manufacturers/2/merge");
        expect(merge?.body).toEqual({ target_id: 1 });
      });
    });
  });

  describe("adding", () => {
    it("keeps Add disabled until there is a name", async () => {
      const user = userEvent.setup();
      renderTable();

      const add = await screen.findByRole("button", { name: "Add" });
      expect(add).toBeDisabled();

      await user.type(screen.getByLabelText("New manufacturer name"), "Elegoo");
      expect(add).toBeEnabled();

      await user.click(add);
      await waitFor(() =>
        expect(calls.some((c) => c.path === "/manufacturers/find-or-create")).toBe(true)
      );
    });
  });
});

/**
 * A table with the field types and reordering that material categories need. Kept separate from
 * the manufacturers harness above so those tests keep proving the additions are opt-in — a table
 * that passes no `reorder` and no checkbox fields must render exactly as it did before.
 */
const FLAG_ROWS = [
  {
    id: 1,
    name: "filament",
    sort_order: 10,
    default_unit: "g",
    consumed_on_failed_build: true,
    usage_count: 4,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    name: "packaging",
    sort_order: 20,
    default_unit: "each",
    consumed_on_failed_build: false,
    usage_count: 0,
    created_at: "2026-01-01T00:00:00Z",
  },
];

function flagRoutes(rows = FLAG_ROWS) {
  return [
    { method: "GET" as const, path: "/material-categories", respond: () => rows },
    {
      method: "PATCH" as const,
      path: /^\/material-categories\/\d+$/,
      respond: (body: unknown) => ({ ...rows[0], ...(body as object) }),
    },
    { method: "POST" as const, path: "/material-categories/reorder", respond: () => rows },
    { method: "POST" as const, path: "/material-categories/find-or-create", respond: (b: unknown) => b },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ] as never[];
}

function FlagHarness({ withReorder = true }: { withReorder?: boolean }) {
  const guard = useUnsavedChangesGuard();
  return (
    <GuardProvider guard={guard}>
      <ReferenceDataTable
        title="Material categories"
        segment="material-categories"
        queryKey={["material-categories"]}
        api={{
          list: materialCategoriesApi.list,
          create: materialCategoriesApi.findOrCreate,
          update: materialCategoriesApi.update,
          ...(withReorder ? { reorder: materialCategoriesApi.reorder } : {}),
        }}
        fields={[
          { key: "name", label: "Name" },
          { key: "consumed_on_failed_build", label: "Consumed by failed builds", type: "checkbox" },
          {
            key: "default_unit",
            label: "Default unit",
            type: "select",
            options: [
              { value: "g", label: "g" },
              { value: "each", label: "each" },
            ],
          },
        ]}
        usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
      />
      <UnsavedChangesDialog {...guard.dialogProps} />
    </GuardProvider>
  );
}

function renderFlagTable(withReorder = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DirtyRegistryProvider>
        <FlagHarness withReorder={withReorder} />
      </DirtyRegistryProvider>
    </QueryClientProvider>
  );
}

describe("ReferenceDataTable field types", () => {
  beforeEach(() => setRoutes(flagRoutes()));

  it("singularises an '-ies' title for the add labels rather than trimming the 's'", async () => {
    renderFlagTable();

    // "Material categories" -> "material category", not the old naive "material categorie".
    expect(await screen.findByText("Add material category")).toBeInTheDocument();
    expect(screen.getByLabelText("New material category name")).toBeInTheDocument();
    expect(screen.queryByText(/categorie\b/)).not.toBeInTheDocument();
  });

  it("sends a real boolean rather than the string it holds in form state", async () => {
    const user = userEvent.setup();
    renderFlagTable();

    await user.click(await screen.findByRole("button", { name: /^filament/ }));
    const flag = screen.getByLabelText("filament Consumed by failed builds");
    expect(flag).toBeChecked();

    await user.click(flag);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch).toBeTruthy();
      // The regression this guards: form state is all strings, so an unconverted send would
      // arrive as "false" — truthy, and silently the opposite of what was ticked.
      expect((patch!.body as Record<string, unknown>).consumed_on_failed_build).toBe(false);
    });
  });

  it("sends an unset select as null, not an empty string", async () => {
    const user = userEvent.setup();
    renderFlagTable();

    await user.click(await screen.findByRole("button", { name: /^filament/ }));
    await user.selectOptions(screen.getByLabelText("filament Default unit"), "");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect((patch!.body as Record<string, unknown>).default_unit).toBeNull();
    });
  });

  it("marks the row dirty when a checkbox is toggled", async () => {
    const user = userEvent.setup();
    renderFlagTable();

    await user.click(await screen.findByRole("button", { name: /^filament/ }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.click(screen.getByLabelText("filament Consumed by failed builds"));
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});

describe("ReferenceDataTable reordering", () => {
  beforeEach(() => setRoutes(flagRoutes()));

  it("posts the swapped order", async () => {
    const user = userEvent.setup();
    renderFlagTable();

    await user.click(await screen.findByRole("button", { name: "Move filament down" }));

    await waitFor(() => {
      const post = calls.find((c) => c.path === "/material-categories/reorder");
      expect(post).toBeTruthy();
      expect((post!.body as { ids: number[] }).ids).toEqual([2, 1]);
    });
  });

  it("disables the arrows at each end of the list", async () => {
    renderFlagTable();

    expect(await screen.findByRole("button", { name: "Move filament up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move packaging down" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move filament down" })).toBeEnabled();
  });

  it("renders no arrows for a table that passes no reorder", async () => {
    renderFlagTable(false);

    await screen.findByRole("button", { name: /^filament/ });
    expect(screen.queryByRole("button", { name: /^Move / })).toBeNull();
  });

  it("does not disturb an open row with unsaved edits", async () => {
    // Reordering changes list order, not which row is expanded, and useEditableCopy is seeded
    // by row.id — so no guard prompt, and the half-typed value survives the refetch. It looks
    // like it should be a problem, which is why it's worth asserting.
    const user = userEvent.setup();
    renderFlagTable();

    await user.click(await screen.findByRole("button", { name: /^filament/ }));
    const name = screen.getByLabelText("filament Name");
    await user.clear(name);
    await user.type(name, "Filament PLA");

    await user.click(screen.getByRole("button", { name: "Move filament down" }));

    await waitFor(() =>
      expect(calls.some((c) => c.path === "/material-categories/reorder")).toBe(true)
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText("filament Name")).toHaveValue("Filament PLA");
  });
});
