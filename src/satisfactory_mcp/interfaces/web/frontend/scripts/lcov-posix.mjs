/* coverage/lcov.info with `/` in every source path, so the Linux scanner container finds the
 * files a Windows run names with `\`. frontend/README.md, "Tests". */

import { readFileSync, writeFileSync } from "node:fs";

const FILE = new URL("../coverage/lcov.info", import.meta.url);

const report = readFileSync(FILE, "utf8");
const posix = report.replace(/^SF:.*$/gm, (line) => line.replaceAll("\\", "/"));
if (posix !== report) {
  writeFileSync(FILE, posix, "utf8");
}
