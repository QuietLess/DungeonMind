// UI5 API client: docs/ARCHITECTURE.md › M7 contract.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, configureApi, request } from "./api";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(
    body === undefined ? null : JSON.stringify(body),
    { status, headers: { "content-type": "application/json" } },
  ));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("UI5 request", () => {
  it("sends unsafe methods with the CSRF header, JSON body and same-origin credentials", async () => {
    const fetch = mockFetch(201, { id: "c1" });
    await expect(request("POST", "/campaigns", { name: "Ashvale" })).resolves.toEqual({ id: "c1" });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/campaigns");
    const headers = new Headers(init.headers);
    expect(headers.get("X-DungeonMind")).toBe("1");
    expect(headers.get("content-type")).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe(JSON.stringify({ name: "Ashvale" }));
  });

  it("GETs don't need the header", async () => {
    const fetch = mockFetch(200, []);
    await request("GET", "/campaigns");
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("X-DungeonMind")).toBeNull();
  });

  it("returns undefined for 204", async () => {
    mockFetch(204, undefined);
    await expect(request("DELETE", "/campaigns/x")).resolves.toBeUndefined();
  });

  it("throws the API's detail message", async () => {
    mockFetch(403, { detail: "Only the DM can do that." });
    const err = await request("POST", "/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.message).toBe("Only the DM can do that.");
  });

  it("a 401 calls the unauthorized handler", async () => {
    const onUnauthorized = vi.fn();
    configureApi({ onUnauthorized });
    mockFetch(401, { detail: "Please sign in." });
    await expect(request("GET", "/me")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("network failures become a readable ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await request("GET", "/me").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toMatch(/reach the server/i);
  });
});
