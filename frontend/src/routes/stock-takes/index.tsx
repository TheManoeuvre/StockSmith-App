import { createFileRoute } from "@tanstack/react-router";

// The list itself lives in route.tsx (the /stock-takes layout), so it stays mounted while
// $stockTakeId renders as a slide-over panel on top of it — see DetailPanel.tsx. This route
// exists only so "/stock-takes" (no id) matches something.
export const Route = createFileRoute("/stock-takes/")({
  component: () => null,
});
