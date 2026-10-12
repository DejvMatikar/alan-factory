import { shortcutHint } from "../lib/shortcuts";
import Button from "./Button";
import { useCommandPalette } from "./commandPaletteContext";
import HoverReveal from "./HoverReveal";
import ShortcutHint from "./ShortcutHint";

export default function CommandPaletteTrigger() {
  const { openPalette } = useCommandPalette();
  const hint = shortcutHint("palette");
  return (
    <span className="hidden sm:inline-flex">
      <HoverReveal
        id="command-palette-trigger-description"
        description="Search and run commands, such as starting a build or exploring a repository."
      >
        <Button
          variant="ghost"
          data-testid="command-palette-trigger"
          onClick={(e) => {
            e.currentTarget.focus();
            openPalette({ name: "root" });
          }}
        >
          Commands <ShortcutHint glyph={hint.glyph} spoken={hint.spoken} />
        </Button>
      </HoverReveal>
    </span>
  );
}
