import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
vi.mock("../client", () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    put: (...a: unknown[]) => put(...a),
  },
}));

import * as maps from "../maps";

beforeEach(() => {
  get.mockReset().mockResolvedValue({ data: { maps: [], marks: {}, work: {} } });
  post.mockReset().mockResolvedValue({ data: {} });
  put.mockReset().mockResolvedValue({ data: {} });
});

describe("knowledge maps API: owners (#180)", () => {
  it("names the map's owner on every call, and sends none when the owner is not known", async () => {
    await maps.fetchMap("shop", "acme");
    await maps.fetchMarks("shop", "ann@example.com", "");
    await maps.fetchWork("shop");
    await maps.fetchPart("shop", "a/b", "acme");
    await maps.markPart("shop", { node_id: "a", verdict: "bloat", by: "ann@example.com" }, "acme");
    await maps.sendParts("shop", { node_ids: ["a"], by: "ann@example.com" }, "acme");
    expect(get.mock.calls[0], "a call must name the map's owner").toEqual([
      "/maps/shop",
      expect.objectContaining({ params: { owner: "acme" } }),
    ]);
    expect(get.mock.calls[1][1].params, "an empty owner names the map of no owner").toEqual({ by: "ann@example.com", owner: "" });
    expect(get.mock.calls[2][1].params, "an owner not known is never sent").toEqual({});
    expect(get.mock.calls[3]).toEqual(["/maps/shop/parts/a%2Fb", { params: { owner: "acme" } }]);
    expect(post.mock.calls[0][2]).toEqual({ params: { owner: "acme" } });
    expect(post.mock.calls[1][2]).toEqual(expect.objectContaining({ params: { owner: "acme" } }));
  });

  it("gives a map another owner, naming the map by its current one", async () => {
    const setMapOwner = (maps as Record<string, unknown>).setMapOwner as
      | ((slug: string, owner: string | undefined, to: string | null) => Promise<unknown>)
      | undefined;
    expect(setMapOwner, "the API must offer an admin a way to set a map's owner").toBeTypeOf("function");
    await setMapOwner?.("org", "", "acme");
    expect(put).toHaveBeenCalledWith("/maps/org/owner", { owner: "acme" }, { params: { owner: "" } });
  });

  it("links a map by its owner, and an older backend's by its slug alone", () => {
    const mapPath = (maps as Record<string, unknown>).mapPath as
      | ((m: { slug: string; owner?: string | null }) => string)
      | undefined;
    expect(mapPath, "a map's link must carry its owner").toBeTypeOf("function");
    expect(mapPath?.({ slug: "shop", owner: "acme" })).toBe("/maps/shop?owner=acme");
    expect(mapPath?.({ slug: "org", owner: null })).toBe("/maps/org?owner=");
    expect(mapPath?.({ slug: "shop" })).toBe("/maps/shop");
  });
});
