import { createFileRoute } from "@tanstack/react-router";

// The list itself lives in route.tsx (the /purchases layout), so it stays mounted while
// $purchaseId/new render as a slide-over panel on top of it — see DetailPanel.tsx. This route
// exists only so "/purchases" (no id) matches something.
export const Route = createFileRoute("/purchases/")({
  component: () => null,
});
