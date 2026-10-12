import DescribedNavLink from "./DescribedNavLink";
import { useHealthContext } from "../api/HealthProvider";
import { deriveHealthDisplay } from "./healthDisplay";

export default function HealthStatusBadge() {
  const { healthState, queuePaused, blockedCount, needsUserCount } = useHealthContext();
  const display = deriveHealthDisplay({ healthState, queuePaused, blockedCount, needsUserCount });

  return (
    <DescribedNavLink
      to="/health"
      ariaLabel={display.ariaLabel}
      testId="nav-health-badge"
      description="Overall system health across every connected repository."
      count={display.count}
      align="end"
      leading={
        <span className="inline-flex h-5 w-2 items-center">
          <span
            data-testid="nav-health-badge-dot"
            aria-hidden="true"
            className={`inline-block h-2 w-2 rounded-full ${display.dot}`}
          />
        </span>
      }
    >
      Health
    </DescribedNavLink>
  );
}
