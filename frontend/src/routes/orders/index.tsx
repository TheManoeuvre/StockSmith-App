import { createFileRoute } from "@tanstack/react-router";

// The list itself lives in route.tsx (the /orders layout), so it stays mounted while
// $orderId/new render as a slide-over panel on top of it — see DetailPanel.tsx. This route
// exists only so "/orders" (no id) matches something.
export const Route = createFileRoute("/orders/")({
  component: () => null,
});
