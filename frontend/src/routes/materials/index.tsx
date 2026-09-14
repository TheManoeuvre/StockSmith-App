import { createFileRoute } from "@tanstack/react-router";

// The list itself lives in route.tsx (the /materials layout), so it stays mounted while
// $materialId renders as a slide-over panel on top of it — see DetailPanel.tsx. This route
// exists only so "/materials" (no id) matches something.
export const Route = createFileRoute("/materials/")({
  component: () => null,
});
