import { describe, expect, it } from "vitest";
import { decodeHtmlEntities, splitPersonalization } from "./personalization";

describe("decodeHtmlEntities", () => {
  it("decodes numeric and named entities", () => {
    expect(decodeHtmlEntities("Pippa&#39;s Library &amp; Co &#x41;")).toBe("Pippa's Library & Co A");
  });
  it("leaves unknown entities alone", () => {
    expect(decodeHtmlEntities("a &bogus; b")).toBe("a &bogus; b");
  });
});

describe("splitPersonalization", () => {
  it("replaces urls with numbered parts", () => {
    const parts = splitPersonalization("Photos: https://x.com/a.jpg?version=0; Photos: https://x.com/b.jpg?version=0");
    const urls = parts.filter((p) => p.kind === "url");
    expect(urls).toHaveLength(2);
    expect(urls[1]).toMatchObject({ index: 2, url: "https://x.com/b.jpg?version=0" });
  });
  it("returns plain text untouched", () => {
    expect(splitPersonalization("Text: Pippa&#39;s")).toEqual([{ kind: "text", text: "Text: Pippa's" }]);
  });
});
