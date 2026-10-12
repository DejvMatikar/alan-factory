import { useInvalidateOperatorStatus, useOperatorStatusQuery } from "../../../api/queries";
import { useChangeRefetch } from "../../../api/ServerEventsProvider";
import DescribedNavLink from "../../../components/DescribedNavLink";

const LIVE_DOT = "bg-green-500";
const IDLE_DOT = "border border-text-muted";

export default function OperatorNavLink() {
  const { data } = useOperatorStatusQuery();
  useChangeRefetch("health", useInvalidateOperatorStatus());
  const live = data !== undefined && data.session !== null;

  return (
    <DescribedNavLink
      to="/operator"
      ariaLabel={live ? "Operator terminal: session live" : "Operator terminal"}
      testId="nav-operator"
      description="A Claude Code terminal for diagnosing and unblocking the fleet."
      align="end"
      leading={
        <span className="inline-flex h-5 w-2 items-center">
          <span
            data-testid="nav-operator-dot"
            data-live={live ? "true" : "false"}
            aria-hidden="true"
            className={`inline-block h-2 w-2 rounded-full ${live ? LIVE_DOT : IDLE_DOT}`}
          />
        </span>
      }
    >
      Operator
    </DescribedNavLink>
  );
}
