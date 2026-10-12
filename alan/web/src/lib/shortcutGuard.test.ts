import { afterEach, describe, expect, it } from "vitest";

import { shortcutsBlocked } from "./shortcutGuard";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("shortcutsBlocked", () => {
  it("is false for a non-editable target with no dialog open", () => {
    expect(shortcutsBlocked(document.body)).toBe(false);
  });

  it("is false for a null target with no dialog open", () => {
    expect(shortcutsBlocked(null)).toBe(false);
  });

  it("is true for an input target", () => {
    const input = document.createElement("input");
    document.body.appendChild(input);
    expect(shortcutsBlocked(input)).toBe(true);
  });

  it("is true for a textarea target", () => {
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    expect(shortcutsBlocked(textarea)).toBe(true);
  });

  it("is true for a select target", () => {
    const select = document.createElement("select");
    document.body.appendChild(select);
    expect(shortcutsBlocked(select)).toBe(true);
  });

  it("is true for a contenteditable='' target", () => {
    const div = document.createElement("div");
    div.setAttribute("contenteditable", "");
    document.body.appendChild(div);
    expect(shortcutsBlocked(div)).toBe(true);
  });

  it("is true for a contenteditable='true' target", () => {
    const div = document.createElement("div");
    div.setAttribute("contenteditable", "true");
    document.body.appendChild(div);
    expect(shortcutsBlocked(div)).toBe(true);
  });

  it("is false for a div with no contenteditable attribute", () => {
    const div = document.createElement("div");
    document.body.appendChild(div);
    expect(shortcutsBlocked(div)).toBe(false);
  });

  it("is true when a dialog is open, even for a non-editable target", () => {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("open", "");
    document.body.appendChild(dialog);
    expect(shortcutsBlocked(document.body)).toBe(true);
  });

  it("is false when a dialog exists but is not open", () => {
    const dialog = document.createElement("dialog");
    document.body.appendChild(dialog);
    expect(shortcutsBlocked(document.body)).toBe(false);
  });
});
