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
