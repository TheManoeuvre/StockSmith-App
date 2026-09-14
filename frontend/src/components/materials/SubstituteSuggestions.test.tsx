import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { SubstituteSuggestion } from "../../api/types";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { SubstituteSuggestions } = await import("./SubstituteSuggestions");

const SUGGESTIONS: SubstituteSuggestion[] = [
  { material_id: 2, material_name: "Medium box", rank: 0, notes: "Fits with padding", available_qty: "20" },
  { material_id: 3, material_name: "Large box", rank: 1, notes: null, available_qty: "3" },
];

function renderSuggestions(props: Partial<React.ComponentProps<typeof SubstituteSuggestions>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SubstituteSuggestions materialId={1} suggestions={SUGGESTIONS} {...props} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  setRoutes([
    {
      method: "POST",
      path: "/material-substitute-usage",
      respond: (body) => ({ id: 1, ...(body as object), created_at: "2026-01-01T00:00:00Z" }),
    },
  ]);
});

it("renders nothing when there are no suggestions", () => {
  const { container } = renderSuggestions({ suggestions: [] });
  expect(container).toBeEmptyDOMElement();
});

it("shows ranked options in rank order", () => {
  renderSuggestions();
  const buttons = screen.getAllByRole("button");
  expect(buttons[0]).toHaveTextContent("Medium box");
  expect(buttons[1]).toHaveTextContent("Large box");
});

it("requires a confirm step before logging usage, and posts the right ids", async () => {
  const user = userEvent.setup();
  renderSuggestions({ orderId: 42, buildId: null });

  await user.click(screen.getByRole("button", { name: /Medium box/ }));
  // Not logged yet — needs the explicit Confirm.
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

  await user.click(screen.getByText("Confirm"));

  await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
  expect(calls[0]).toMatchObject({
    method: "POST",
    path: "/material-substitute-usage",
    body: { material_id: 1, substitute_material_id: 2, order_id: 42, build_id: null },
  });
  expect(await screen.findByText(/Logged — using Medium box instead/)).toBeInTheDocument();
});

it("cancelling a pending pick leaves nothing logged", async () => {
  const user = userEvent.setup();
  renderSuggestions();

  await user.click(screen.getByRole("button", { name: /Medium box/ }));
  await user.click(screen.getByText("Cancel"));

  expect(screen.getByRole("button", { name: /Medium box/ })).toBeInTheDocument();
  expect(calls).toHaveLength(0);
});
