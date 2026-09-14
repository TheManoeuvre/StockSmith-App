import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Reference data moved inline into Settings → Reference data, so this page no longer exists.
 *
 * Kept as a redirect rather than deleted: the file is what generates the route in
 * routeTree.gen.ts, and a bookmark or an old link landing on a 404 is a worse outcome than one
 * extra hop. Nothing in the app links here any more.
 */
export const Route = createFileRoute("/manufacturers/")({
  beforeLoad: () => {
    throw redirect({ to: "/settings", search: { page: "lists" } });
  },
});
