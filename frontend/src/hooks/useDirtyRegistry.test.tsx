import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  DirtyPath,
  DirtyRegistry,
  DirtyRegistryProvider,
  useDirtyRegistration,
  useDirtyRegistryApi,
} from "./useDirtyRegistry";

describe("DirtyRegistry", () => {
  it("reports nothing dirty when empty", () => {
    const r = new DirtyRegistry();
    expect(r.isDirtyUnder()).toBe(false);
    expect(r.dirtyLabelsUnder()).toEqual([]);
  });

  it("only counts entries that are actually dirty", () => {
    const r = new DirtyRegistry();
    r.set("bom", "Build BOM", false);
    expect(r.isDirtyUnder()).toBe(false);
    r.set("bom", "Build BOM", true);
    expect(r.isDirtyUnder()).toBe(true);
    expect(r.dirtyLabelsUnder()).toEqual(["Build BOM"]);
  });

  it("scopes queries by path prefix", () => {
    const r = new DirtyRegistry();
    r.set("variant-1/bom-overrides", "Variant 1 BOM", true);
    r.set("variant-2/rename", "Variant 2", false);

    expect(r.isDirtyUnder("variant-1/")).toBe(true);
    expect(r.isDirtyUnder("variant-2/")).toBe(false);
    expect(r.isDirtyUnder("")).toBe(true);
  });

  it("does not let variant-1/ match variant-12/", () => {
    // The trailing slash is what makes this safe — without it, collapsing variant 1 would
    // prompt about variant 12's edits.
    const r = new DirtyRegistry();
    r.set("variant-12/rename", "Variant 12", true);
    expect(r.isDirtyUnder("variant-1/")).toBe(false);
    expect(r.isDirtyUnder("variant-12/")).toBe(true);
  });

  it("deduplicates labels", () => {
    const r = new DirtyRegistry();
    r.set("a", "Pricing", true);
    r.set("b", "Pricing", true);
    expect(r.dirtyLabelsUnder()).toEqual(["Pricing"]);
  });

  it("forgets an entry once removed", () => {
    const r = new DirtyRegistry();
    r.set("bom", "Build BOM", true);
    r.remove("bom");
    expect(r.isDirtyUnder()).toBe(false);
  });

  it("keeps getSnapshot referentially stable when nothing changed", () => {
    // useSyncExternalStore re-renders forever if the snapshot identity changes on every read.
    const r = new DirtyRegistry();
    r.set("bom", "Build BOM", true);
    const first = r.getSnapshot();
    r.set("bom", "Build BOM", true); // same values again
    expect(r.getSnapshot()).toBe(first);

    r.set("kitting-bom", "Kitting BOM", false); // still no NEW dirty path
    expect(r.getSnapshot()).toBe(first);

    r.set("kitting-bom", "Kitting BOM", true); // now it changes
    expect(r.getSnapshot()).not.toBe(first);
  });

  it("only notifies subscribers when the dirty set changes", () => {
    const r = new DirtyRegistry();
    let calls = 0;
    r.subscribe(() => calls++);

    r.set("bom", "Build BOM", true);
    expect(calls).toBe(1);
    r.set("bom", "Build BOM", true);
    expect(calls).toBe(1);
    r.set("bom", "Build BOM", false);
    expect(calls).toBe(2);
  });
});

function Editor({ dirty, label = "Editor", localKey = "e" }: { dirty: boolean; label?: string; localKey?: string }) {
  useDirtyRegistration(localKey, label, dirty);
  return null;
}

/** Hands the enclosing provider's registry out to the test so it can be queried directly. */
function Probe({ onRead }: { onRead: (r: DirtyRegistry) => void }) {
  const registry = useDirtyRegistryApi();
  onRead(registry);
  return null;
}

describe("registry integration", () => {
  it("nests paths through DirtyPath and unregisters on unmount", () => {
    let registry!: DirtyRegistry;
    const capture = (r: DirtyRegistry) => {
      registry = r;
    };

    const tree = (showRow: boolean) => (
      <DirtyRegistryProvider>
        <Probe onRead={capture} />
        {showRow && (
          <DirtyPath segment="variant-3">
            <Editor dirty localKey="bom-overrides" label="Variant 3 BOM" />
          </DirtyPath>
        )}
      </DirtyRegistryProvider>
    );

    const { rerender } = render(tree(true));
    expect(registry.isDirtyUnder("variant-3/")).toBe(true);
    expect(registry.dirtyLabelsUnder("variant-3/")).toEqual(["Variant 3 BOM"]);

    // Collapsing the row unmounts its editors — the registry must forget them, or the page
    // would claim unsaved changes forever.
    rerender(tree(false));
    expect(registry.isDirtyUnder("")).toBe(false);
  });

  it("keeps sibling subtrees independent", () => {
    let registry!: DirtyRegistry;
    render(
      <DirtyRegistryProvider>
        <Probe onRead={(r) => (registry = r)} />
        <DirtyPath segment="variant-1">
          <Editor dirty localKey="rename" label="Variant 1" />
        </DirtyPath>
        <DirtyPath segment="variant-2">
          <Editor dirty={false} localKey="rename" label="Variant 2" />
        </DirtyPath>
      </DirtyRegistryProvider>
    );

    expect(registry.isDirtyUnder("variant-1/")).toBe(true);
    expect(registry.isDirtyUnder("variant-2/")).toBe(false);
  });

  it("is inert outside a provider, so shared editors work on other routes", () => {
    // DefaultKittingBomSettings uses the same hooks on the settings route, which has no
    // provider and deliberately gets no unsaved-changes prompt.
    expect(() => render(<Editor dirty />)).not.toThrow();
  });
});
