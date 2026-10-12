import { useProjectFeatures } from "../hooks/useProjectFeatures";
import { writeProjectFeaturesOn } from "../projectFeatures";
import Button, { CheckIcon } from "./Button";
import HoverReveal from "./HoverReveal";

export default function ProjectFeaturesToggle() {
  const on = useProjectFeatures();
  return (
    <HoverReveal
      id="project-features-toggle-description"
      description="Shows or hides project features in the interface. Nothing in the data changes: projects, their repository bindings and their recorded spend are untouched."
    >
      <Button
        variant="ghost"
        aria-pressed={on}
        data-testid="project-features-toggle"
        className={on ? "gap-1.5 bg-surface-elevated" : "gap-1.5"}
        onClick={() => {
          writeProjectFeaturesOn(!on);
        }}
      >
        {on && <CheckIcon />}
        Projects
      </Button>
    </HoverReveal>
  );
}
