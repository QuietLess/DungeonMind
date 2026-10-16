// Regression: an effect returned scrollIntoView's result; Chrome now returns a Promise from it,
// which React then called as a cleanup ("destroy is not a function") and the chat page crashed.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

it("no effect is an expression-bodied arrow (its value would become the cleanup)", () => {
  const offenders = files(__dirname)
    .filter((f) => f.endsWith(".tsx") || (f.endsWith(".ts") && !f.endsWith(".test.ts")))
    .filter((f) => /useEffect\(\s*\(\)\s*=>\s*[^{\s]/.test(readFileSync(f, "utf8")));
  expect(offenders).toEqual([]);
});
