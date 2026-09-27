/* Regenerate src/api-schema.d.ts from a running server, then stamp its header.
 *
 * The port is the first argument (`npm run typegen -- 8931`), else SATISFACTORY_WEB_PORT,
 * else 8712. A throwaway server on its own port keeps the player's server out of it.
 */

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const port = process.argv[2] || process.env.SATISFACTORY_WEB_PORT || "8712";
if (!/^\d+$/.test(port)) {
  console.error(`typegen: the port must be a number, not ${JSON.stringify(port)}`);
  process.exit(2);
}

const cli = fileURLToPath(new URL("../node_modules/openapi-typescript/bin/cli.js", import.meta.url));
const out = fileURLToPath(new URL("../src/api-schema.d.ts", import.meta.url));
const url = `http://127.0.0.1:${port}/openapi.json`;

const run = spawnSync(process.execPath, [cli, url, "-o", out], { stdio: "inherit" });
if (run.status !== 0) process.exit(run.status ?? 1);
await import("./stamp-schema.mjs");
