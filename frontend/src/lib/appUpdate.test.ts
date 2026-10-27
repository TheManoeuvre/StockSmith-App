import { describe, expect, it } from "vitest";
import { compareVersions, isUpdateAvailable } from "./appUpdate";

describe("compareVersions", () => {
  it("compares numerically, not as strings", () => {
    expect(compareVersions("0.9.0", "0.24.0")).toBe(-1);
    expect(compareVersions("0.24.1", "0.24.0")).toBe(1);
    expect(compareVersions("1.0.0", "1.0.0")).toBe(0);
  });

  it("tolerates a v prefix and a prerelease suffix", () => {
    expect(compareVersions("v0.25.0", "0.25.0")).toBe(0);
    expect(compareVersions("0.25.0-beta.1", "0.24.0")).toBe(1);
  });

  it("treats garbage as 0.0.0 so it never looks newer", () => {
    expect(compareVersions("nonsense", "0.1.0")).toBe(-1);
  });
});

describe("isUpdateAvailable", () => {
  it("is available when the release is newer than the install and not ignored", () => {
    expect(isUpdateAvailable("0.25.0", "0.24.0", null)).toBe(true);
  });

  it("is not available when up to date or when nothing was offered", () => {
    expect(isUpdateAvailable("0.24.0", "0.24.0", null)).toBe(false);
    expect(isUpdateAvailable(null, "0.24.0", null)).toBe(false);
  });

  it("hides the release the user ignored", () => {
    expect(isUpdateAvailable("0.25.0", "0.24.0", "0.25.0")).toBe(false);
  });

  it("resumes when a newer release than the ignored one appears", () => {
    expect(isUpdateAvailable("0.26.0", "0.24.0", "0.25.0")).toBe(true);
  });

  it("does not resurface an ignored version if the feed rolls back to an older release", () => {
    expect(isUpdateAvailable("0.25.0", "0.24.0", "0.26.0")).toBe(false);
  });
});
