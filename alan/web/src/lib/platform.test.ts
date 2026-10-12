import { afterEach, describe, expect, it, vi } from "vitest";

import { isApplePlatform } from "./platform";

const MAC_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const IPHONE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148";
const IPAD_UA =
  "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148";
const IPOD_UA =
  "Mozilla/5.0 (iPod touch; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko)";
const WINDOWS_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function stubUserAgent(userAgent: string): void {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
}

function stubUserAgentData(value: { platform?: string }): void {
  Object.defineProperty(navigator, "userAgentData", { configurable: true, value });
}

afterEach(() => {
  Reflect.deleteProperty(navigator, "userAgentData");
});

describe("isApplePlatform", () => {
  it.each([
    ["a Mac", MAC_UA],
    ["an iPhone", IPHONE_UA],
    ["an iPad", IPAD_UA],
    ["an iPod", IPOD_UA],
  ])("treats %s user agent as Apple when there is no platform hint", (_label, userAgent) => {
    stubUserAgent(userAgent);
    expect(isApplePlatform()).toBe(true);
  });

  it.each([
    ["Windows", WINDOWS_UA],
    ["Linux", LINUX_UA],
  ])("treats a %s user agent as not Apple when there is no platform hint", (_label, userAgent) => {
    stubUserAgent(userAgent);
    expect(isApplePlatform()).toBe(false);
  });

  it("prefers the platform hint over the user agent when it names an Apple platform", () => {
    stubUserAgent(WINDOWS_UA);
    stubUserAgentData({ platform: "macOS" });
    expect(isApplePlatform()).toBe(true);
  });

  it("prefers the platform hint over the user agent when it names another platform", () => {
    stubUserAgent(MAC_UA);
    stubUserAgentData({ platform: "Windows" });
    expect(isApplePlatform()).toBe(false);
  });

  it("falls back to the user agent when the hint carries no platform", () => {
    stubUserAgent(MAC_UA);
    stubUserAgentData({});
    expect(isApplePlatform()).toBe(true);
  });

  it("reads the platform afresh on every call", () => {
    const userAgent = vi.spyOn(navigator, "userAgent", "get");
    userAgent.mockReturnValue(LINUX_UA);
    expect(isApplePlatform()).toBe(false);
    userAgent.mockReturnValue(MAC_UA);
    expect(isApplePlatform()).toBe(true);
  });
});
