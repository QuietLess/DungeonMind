// UI3: no raw HTML injection anywhere in the app source.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

it("no source file uses dangerouslySetInnerHTML or innerHTML", () => {
  const offenders = files(join(__dirname))
    .filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith("no-raw-html.test.ts"))
    .filter((f) => /dangerouslySetInnerHTML|\.innerHTML\s*=|outerHTML\s*=/.test(readFileSync(f, "utf8")));
  expect(offenders).toEqual([]);
});
