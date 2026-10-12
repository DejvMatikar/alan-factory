import { afterEach, describe, expect, it } from "vitest";

import { readOperatorTheme } from "./operatorTheme";

const TOKENS = ["--color-surface", "--color-text-primary", "--color-border"];

afterEach(() => {
  for (const token of TOKENS) document.documentElement.style.removeProperty(token);
});

describe("readOperatorTheme", () => {
  it("maps the design-system tokens onto the terminal theme", () => {
    document.documentElement.style.setProperty("--color-surface", " #010203 ");
    document.documentElement.style.setProperty("--color-text-primary", "#040506");
    document.documentElement.style.setProperty("--color-border", "#070809");
    expect(readOperatorTheme()).toEqual({
      background: "#010203",
      foreground: "#040506",
      cursor: "#040506",
      cursorAccent: "#010203",
      selectionBackground: "#070809",
    });
  });

  it("leaves a color out when its token is undefined so xterm keeps its own default", () => {
    document.documentElement.style.setProperty("--color-surface", "#010203");
    expect(readOperatorTheme()).toEqual({ background: "#010203", cursorAccent: "#010203" });
  });

  it("returns an empty theme when no token is defined", () => {
    expect(readOperatorTheme()).toEqual({});
  });

  it("reads the tokens from the root it is given", () => {
    const root = document.createElement("div");
    root.style.setProperty("--color-border", "#0a0b0c");
    document.body.append(root);
    expect(readOperatorTheme(root)).toEqual({ selectionBackground: "#0a0b0c" });
    root.remove();
  });
});
