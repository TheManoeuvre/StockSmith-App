import { describe, expect, it } from "vitest";
import { parseReleaseNotes, splitBold } from "./releaseNotes";

describe("parseReleaseNotes", () => {
  it("groups bullets under their heading and joins wrapped lines", () => {
    const body = [
      "### Added",
      "",
      "- **Copy buttons.** One next to the order",
      "  number, and one on personalisation.",
      "- **Value column.** Prices each line.",
      "",
      "### Fixed",
      "- Something small.",
    ].join("\n");
    expect(parseReleaseNotes(body)).toEqual([
      {
        heading: "Added",
        items: [
          "**Copy buttons.** One next to the order number, and one on personalisation.",
          "**Value column.** Prices each line.",
        ],
      },
      { heading: "Fixed", items: ["Something small."] },
    ]);
  });

  it("copes with bullets that have no heading, and with CRLF line endings", () => {
    expect(parseReleaseNotes("- One\r\n- Two")).toEqual([{ heading: null, items: ["One", "Two"] }]);
  });

  it("drops headings with nothing under them", () => {
    expect(parseReleaseNotes("### Empty\n### Added\n- x")).toEqual([{ heading: "Added", items: ["x"] }]);
  });
});

describe("splitBold", () => {
  it("separates bold runs from plain text", () => {
    expect(splitBold("**Headline.** and detail")).toEqual([
      { text: "Headline.", bold: true },
      { text: " and detail", bold: false },
    ]);
  });

  it("leaves text without markers alone", () => {
    expect(splitBold("plain")).toEqual([{ text: "plain", bold: false }]);
  });
});
