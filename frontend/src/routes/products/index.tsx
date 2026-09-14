import { createFileRoute } from "@tanstack/react-router";

// The list itself lives in route.tsx (the /products layout), so it stays mounted while
// $productId renders as a slide-over panel on top of it — see DetailPanel.tsx. This route
// exists only so "/products" (no id) matches something.
export const Route = createFileRoute("/products/")({
  component: () => null,
});
