/* `npm test`: the unit tests under test/, run in node with no DOM, and coverage of every source
 * module written to coverage/lcov.info with repository-relative paths, which is what Sonar
 * resolves. What is tested and why: frontend/README.md, "Tests". */

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
    restoreMocks: true,
    unstubGlobals: true,
    unstubEnvs: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/**/*.d.ts"],
      reporter: ["text-summary", ["lcovonly", { projectRoot: "../../../../.." }]],
      reportsDirectory: "coverage",
    },
  },
});
