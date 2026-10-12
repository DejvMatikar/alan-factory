const EDITABLE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

// jsdom (used by this project's unit tests) does not implement the
// `isContentEditable` IDL property, so this reads the attribute directly —
// it also keeps the check to plain DOM reads with no framework-state
// threading, matching the guard's own "one query" contract below.
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (EDITABLE_TAGS.has(target.tagName)) return true;
  const contentEditable = target.getAttribute("contenteditable");
  return contentEditable === "" || contentEditable === "true";
}

// A single DOM query stands in for "is any modal currently open" — every
// modal in this app renders a native <dialog> shown via showModal(), so this
// generalizes across all of them without a caller having to thread an
// open/closed boolean in for each one it might collide with.
export function shortcutsBlocked(target: EventTarget | null): boolean {
  return isEditableTarget(target) || document.querySelector("dialog[open]") !== null;
}
