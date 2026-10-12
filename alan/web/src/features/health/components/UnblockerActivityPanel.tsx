import { useId, useState } from "react";

import type { UnblockActivityResponse } from "../../../api";
import { MetricCell } from "./HealthMetricsPanel";
import {
  plural,
  unblockerCounts,
  unblockerDigestLine,
} from "../../../lib/unblockerSummaryFields";
import UnblockerParkKindsTable from "./UnblockerParkKindsTable";
import UnblockerWorkspacesTable from "./UnblockerWorkspacesTable";
import { useUnblockerActivity } from "../../../hooks/useUnblockerActivity";
import Disclosure from "../../../ui/Disclosure";

const _NOTICE_CLASS = "text-sm text-text-muted";

const _CLEARANCE_NOTE =
  "Cleared means the workspace is not stuck right now. A human merge, an archive or a manual answer clears it too, so it is not proof that the unblocker's action worked.";

const _WORKSPACES_UNAVAILABLE =
  "Per-workspace activity is listed for one repository at a time, so it is not available across all repositories. Open a repository from the repository health table to see its workspaces.";

interface Notice {
  readonly testId: string;
  readonly text: string;
}

const _NEVER_RECORDED: Notice = {
  testId: "unblocker-activity-empty-never",
  text: "The unblocker has never recorded any activity.",
};

function emptyNoticeOf(activity: UnblockActivityResponse): Notice | null {
  const recorded = activity.scope === "fleet" ? activity.ever_recorded : activity.fleet_ever_recorded;
  if (!recorded) return _NEVER_RECORDED;
  if (activity.events_total > 0 || activity.waited_workspaces > 0) return null;
  if (activity.scope === "fleet") {
    return {
      testId: "unblocker-activity-empty-window",
      text: `Nothing happened in the last ${activity.window_days} days. The unblocker has recorded activity before that.`,
    };
  }
  return {
    testId: "unblocker-activity-empty-repo-window",
    text: `No unblocker activity for ${activity.repo} in the last ${activity.window_days} days (the fleet has recorded activity elsewhere).`,
  };
}

function scopeLabelOf(activity: UnblockActivityResponse): string {
  return activity.scope === "repo" ? activity.repo : "all repositories";
}

function SummaryRow({ activity }: Readonly<{ activity: UnblockActivityResponse }>) {
  const days = activity.window_days;
  return (
    <div
      data-testid="unblocker-activity-summary"
      className="mt-3 flex flex-wrap items-start gap-x-8 gap-y-3"
    >
      <MetricCell
        label="Events in window"
        value={String(activity.events_total)}
        valueTestid="unblocker-metric-events"
        explainer={`Attempts and give-up reports recorded in the last ${days} days. Waiting is not an event.`}
      />
      <MetricCell
        label="Workspaces touched"
        value={String(activity.workspaces_touched)}
        valueTestid="unblocker-metric-touched"
        explainer="Distinct workspaces with at least one attempt or give-up in the window."
      />
      <MetricCell
        label="Waiting only — host has not acted or given up"
        value={String(activity.waited_workspaces)}
        valueTestid="unblocker-metric-waited"
        explainer="Workspaces the unblocker only waited on in the window: no attempt and no give-up recorded."
      />
      <MetricCell
        label="Diagnosis coverage"
        value={`${activity.diagnosed_workspaces} of ${activity.diagnosable_workspaces}`}
        valueTestid="unblocker-metric-diagnosis"
        explainer={`${activity.diagnosed_workspaces} of ${activity.diagnosable_workspaces} rescued workspaces had a diagnosis.`}
      />
    </div>
  );
}

function FleetDetail({ unattributedEvents }: Readonly<{ unattributedEvents: number }>) {
  return (
    <>
      <p data-testid="unblocker-workspaces-unavailable" className="mt-4 text-xs text-text-muted">
        {_WORKSPACES_UNAVAILABLE}
      </p>
      {unattributedEvents > 0 && (
        <p data-testid="unblocker-unattributed" className="mt-2 text-xs text-text-muted">
          {plural(unattributedEvents, "event")} could not be attributed to a repository.
        </p>
      )}
    </>
  );
}

function ScopeDetail({ activity }: Readonly<{ activity: UnblockActivityResponse }>) {
  if (activity.scope === "repo") return <UnblockerWorkspacesTable activity={activity} />;
  return <FleetDetail unattributedEvents={activity.unattributed_events} />;
}

interface ActivityDisclosureProps {
  readonly activity: UnblockActivityResponse;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

function ActivityDisclosure({ activity, open, onOpenChange }: Readonly<ActivityDisclosureProps>) {
  return (
    <Disclosure
      preset="panel"
      defaultOpen={false}
      open={open}
      onOpenChange={onOpenChange}
      testId="unblocker-activity-disclosure"
      summaryTestId="unblocker-activity-toggle"
      className="mt-2"
      label={<span>Unblocker activity — {unblockerDigestLine(unblockerCounts(activity))}</span>}
    >
      <SummaryRow activity={activity} />
      <p data-testid="unblocker-clearance-note" className="mt-2 text-xs text-text-muted">
        {_CLEARANCE_NOTE}
      </p>
      <UnblockerParkKindsTable aggregates={activity.aggregates} scopeLabel={scopeLabelOf(activity)} />
      <ScopeDetail activity={activity} />
    </Disclosure>
  );
}

interface ActivityBodyProps {
  readonly activity: UnblockActivityResponse;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

function ActivityBody({ activity, open, onOpenChange }: Readonly<ActivityBodyProps>) {
  const notice = emptyNoticeOf(activity);
  return (
    <>
      <p data-testid="unblocker-activity-subtitle" className="text-xs text-text-muted">
        What the unblocker did in the last {activity.window_days} days — {scopeLabelOf(activity)}.
        Read-only.
      </p>
      {notice !== null ? (
        <p data-testid={notice.testId} className={`mt-3 ${_NOTICE_CLASS}`}>
          {notice.text}
        </p>
      ) : (
        <ActivityDisclosure activity={activity} open={open} onOpenChange={onOpenChange} />
      )}
    </>
  );
}

interface PanelBodyProps {
  readonly snapshot: UnblockActivityResponse | null;
  readonly error: boolean;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

function PanelBody({ snapshot, error, open, onOpenChange }: Readonly<PanelBodyProps>) {
  if (snapshot !== null) {
    return (
      <>
        {error && (
          <p role="alert" data-testid="unblocker-activity-stale" className={_NOTICE_CLASS}>
            Showing the last loaded activity. The latest refresh failed.
          </p>
        )}
        <ActivityBody activity={snapshot} open={open} onOpenChange={onOpenChange} />
      </>
    );
  }
  if (error) {
    return (
      <p role="alert" data-testid="unblocker-activity-error" className={_NOTICE_CLASS}>
        Unblocker activity could not be loaded. Try again shortly.
      </p>
    );
  }
  return (
    <p data-testid="unblocker-activity-loading" className={_NOTICE_CLASS}>
      Loading unblocker activity…
    </p>
  );
}

export interface UnblockerActivityPanelProps {
  readonly engine: string | null;
}

export default function UnblockerActivityPanel({ engine }: Readonly<UnblockerActivityPanelProps>) {
  const { snapshot, loaded, error } = useUnblockerActivity(engine);
  const headingId = useId();
  const [open, setOpen] = useState(false);
  return (
    <section
      data-testid="unblocker-activity-panel"
      aria-labelledby={headingId}
      aria-busy={!loaded}
      className="mb-4 rounded-md border border-border/60 bg-surface/40 p-3"
    >
      <h2 id={headingId} className="text-lg font-semibold text-text-primary">
        Unblocker activity
      </h2>
      <PanelBody snapshot={snapshot} error={error} open={open} onOpenChange={setOpen} />
    </section>
  );
}
