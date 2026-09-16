// Compiles the app's Tailwind v4 stylesheet (src/index.css) into design-system/dist/styles.css
// for the design-sync export. Tailwind only emits utilities it sees used, so the scan covers
// the app source plus the authored preview stories under .design-sync/previews — a class that
// appears in neither doesn't exist in the compiled sheet. Uses the same @tailwindcss/node +
// oxide pair the Vite plugin uses, so the output matches the app build byte-for-byte in intent.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "@tailwindcss/node";
import { Scanner } from "@tailwindcss/oxide";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "..");
const input = resolve(frontend, "src/index.css");

const compiler = await compile(readFileSync(input, "utf8"), {
  base: dirname(input),
  onDependency: () => {},
});
const scanner = new Scanner({
  sources: [
    { base: resolve(frontend, "src"), pattern: "**/*.{tsx,ts,html}", negated: false },
    { base: resolve(frontend, "../.design-sync/previews"), pattern: "**/*.tsx", negated: false },
    ...compiler.sources,
  ],
});
const css = compiler.build(scanner.scan());
mkdirSync(resolve(here, "dist"), { recursive: true });
writeFileSync(resolve(here, "dist/styles.css"), css);
console.log(`styles.css: ${css.length} bytes from ${scanner.files.length} files`);
