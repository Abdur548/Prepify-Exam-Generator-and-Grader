import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Separate from `vite.config.ts` on purpose.
 *
 * Vitest 3 types the `test` block only through its own `defineConfig`, and that
 * one carries vitest's bundled rolldown-based vite types — which collide with
 * this project's vite 8 plugin types the moment both are in one file. Two
 * attempts at keeping it in `vite.config.ts` produced two different tsc errors
 * while the tests themselves ran perfectly, which is the worst kind of red:
 * nothing wrong with the code, and a build that will not go green.
 *
 * This file is excluded from `tsconfig.node.json` for the same reason, so the
 * clash never reaches `tsc -b`. Vitest reads this in preference to the vite
 * config; the dev server and the production build are untouched by it.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    // jsdom, because every defect these tests exist for was a RENDERING or
    // event-handling bug — a stale flag dropping setState, a card claiming a
    // press, a component mounted in the wrong element. None of them is visible
    // to a test that only calls functions.
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
