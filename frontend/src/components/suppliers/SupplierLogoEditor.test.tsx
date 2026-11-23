import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Supplier } from "../../api/types";
import { SupplierLogoEditor } from "./SupplierLogoEditor";

vi.mock("../../hooks/useSupplierLogoUrl", () => ({ useSupplierLogoUrl: () => null }));
vi.mock("../../lib/tauri", () => ({ pickFile: () => Promise.resolve(null) }));
vi.mock("../../api/suppliers", () => ({ suppliersApi: {} }));

function supplier(overrides: Partial<Supplier>): Supplier {
  return {
    id: 1,
    name: "Acme",
    usage_count: 0,
    website_url: null,
    logo_source: null,
    logo_updated_at: null,
    default_lead_time_days: null,
    ...overrides,
  } as Supplier;
}

function renderEditor(s: Supplier) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SupplierLogoEditor supplier={s} />
    </QueryClientProvider>
  );
}

describe("SupplierLogoEditor", () => {
  it("with no website and no logo, only offers an upload", () => {
    renderEditor(supplier({}));
    expect(screen.getByText(/Add a website to fetch its icon/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload logo…" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /icon/i })).not.toBeInTheDocument();
  });

  it("with a website but no icon found, offers to fetch it", () => {
    renderEditor(supplier({ website_url: "https://acme.example" }));
    expect(screen.getByText(/No icon found on the website/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fetch icon" })).toBeInTheDocument();
  });

  it("a fetched favicon can be refreshed or overridden", () => {
    renderEditor(supplier({ website_url: "https://acme.example", logo_source: "favicon", logo_updated_at: "2026-01-01T00:00:00Z" }));
    expect(screen.getByRole("button", { name: "Refresh icon" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload logo…" })).toBeInTheDocument();
  });

  it("an uploaded logo says it overrides the icon and can revert to it", () => {
    renderEditor(supplier({ website_url: "https://acme.example", logo_source: "upload", logo_updated_at: "2026-01-01T00:00:00Z" }));
    expect(screen.getByText(/overrides the website's icon/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use website icon" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh icon" })).not.toBeInTheDocument();
  });

  it("an uploaded logo with no website can only be removed", () => {
    renderEditor(supplier({ logo_source: "upload", logo_updated_at: "2026-01-01T00:00:00Z" }));
    expect(screen.getByRole("button", { name: "Remove logo" })).toBeInTheDocument();
  });
});
