import { useEffect, useRef, type ReactNode } from "react";

import type { RunSummary } from "../../../api";
import { SCROLL_BODY_FLOW_CLASS, SCROLL_COLUMN_FLOW_CLASS } from "../../../shell/stickyOffsets";
import RunPricing from "../../../components/RunPricing";
import UnderlineTab from "../../../components/UnderlineTab";
import { handleTablistKeyDown } from "../../../components/tablistKeys";

interface ChatScaffoldProps {
  title: string;
  tab: string;
  onTab: (t: "chat" | "summary") => void;
  runSummary?: RunSummary | null;
  collisionRunSummary?: RunSummary | null;
  focusHeadingKey?: string | null;
  autoDispatchStatus?: ReactNode;
  children: ReactNode;
}

export default function ChatScaffold({
  title,
  tab,
  onTab,
  runSummary,
  collisionRunSummary,
  focusHeadingKey = null,
  autoDispatchStatus,
  children,
}: Readonly<ChatScaffoldProps>) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusHeadingKey === null) return;
    headingRef.current?.focus();
  }, [focusHeadingKey]);

  return (
    <div className={`flex flex-col gap-3 ${SCROLL_COLUMN_FLOW_CLASS}`}>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-base font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            {title}
          </h1>
        </div>

        <RunPricing
          runs={[
            { key: "conversation", label: "Conversation", runSummary },
            { key: "collision", label: "Collision check", runSummary: collisionRunSummary },
          ]}
        />

        <div
          role="tablist"
          tabIndex={-1}
          className="flex gap-4 border-b border-border"
          onKeyDown={handleTablistKeyDown}
        >
          <UnderlineTab id="chat" label="Chat" active={tab === "chat"} onClick={() => onTab("chat")} />
          <UnderlineTab
            id="summary"
            label="Summary"
            active={tab === "summary"}
            onClick={() => onTab("summary")}
          />
        </div>
      </div>

      {autoDispatchStatus}

      <div
        role="tabpanel"
        id={`tabpanel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        className={`flex flex-col ${SCROLL_BODY_FLOW_CLASS}`}
      >
        {children}
      </div>
    </div>
  );
}
