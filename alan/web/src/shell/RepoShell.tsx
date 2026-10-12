import { Outlet, useNavigate, useParams, useSearchParams } from "react-router-dom";

import Button from "../components/Button";
import { titleCaseRepoName } from "../lib/fold";
import {
  ACCENT_FRAME_PAD_BOTTOM_CLASS,
  ACCENT_FRAME_PAD_LEFT_CLASS,
  ACCENT_FRAME_PAD_RIGHT_CLASS,
  ACCENT_FRAME_PAD_TOP_CLASS,
  accentEdgeClass,
  accentGlowClass,
  accentVars,
  resolveAccents,
} from "../lib/repoAccent";
import { ChatDeleteAnnouncementRegion } from "../hooks/useChatDelete";
import { useEngines } from "../hooks/useEngines";
import ConversationRail from "./ConversationRail";
import RepoDrawer from "./RepoDrawer";
import { STICKY_SLOT_TOP_CLASS } from "./stickyOffsets";
import { useRailCollapse } from "./useRailCollapse";

interface CollapsedRailIndicatorProps {
  readonly title: string;
  readonly engineKey: string;
  readonly onExpand: () => void;
}

function CollapsedRailIndicator({ title, engineKey, onExpand }: Readonly<CollapsedRailIndicatorProps>) {
  const navigate = useNavigate();
  const handleQuickBuild = (): void => {
    Promise.resolve(
      navigate(`/repos/${engineKey}/build?engine=${encodeURIComponent(engineKey)}`),
    ).catch(
      /* v8 ignore next */
      () => undefined,
    );
  };
  return (
    <div
      className="flex w-14 flex-col items-center gap-2 py-1"
      data-testid={`rail-collapsed-${engineKey}`}
    >
      <Button
        variant="ghost"
        iconOnly
        onClick={onExpand}
        aria-label={`Expand the conversations rail for ${title}`}
        aria-expanded={false}
        data-testid="rail-expand"
      >
        {title.slice(0, 2).toUpperCase()}
      </Button>
      <Button
        variant="ghost"
        iconOnly
        onClick={handleQuickBuild}
        aria-label="Start a build conversation"
        data-testid="rail-collapsed-start-build"
      >
        +
      </Button>
    </div>
  );
}

export default function RepoShell() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  const scopedKey = owner !== undefined && repo !== undefined ? `${owner}/${repo}` : null;
  const { allEngines } = useEngines();
  const resolvedAccent = scopedKey === null ? null : (resolveAccents(allEngines).get(scopedKey) ?? null);
  const { collapsed, toggle } = useRailCollapse();
  const [searchParams, setSearchParams] = useSearchParams();
  const drawerOpen = searchParams.get("drawer") === "open";

  const openDrawer = (): void => {
    const next = new URLSearchParams(searchParams);
    next.set("drawer", "open");
    setSearchParams(next);
  };

  const closeDrawer = (): void => {
    const next = new URLSearchParams(searchParams);
    next.delete("drawer");
    setSearchParams(next, { replace: true });
  };

  if (scopedKey === null) return null;
  const title = titleCaseRepoName(scopedKey);

  return (
    <div className="space-y-4" data-testid="repo-page">
      <div className="flex items-center justify-between gap-2 sm:hidden">
        <button
          type="button"
          onClick={openDrawer}
          aria-label={`Repository menu — ${title}`}
          data-testid="repo-drawer-trigger"
          className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-border px-2 text-sm font-medium text-text-primary hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 sm:hidden"
        >
          {title}
        </button>
      </div>
      <ChatDeleteAnnouncementRegion />
      <div className="flex gap-6">
        <div
          style={accentVars(resolvedAccent)}
          className={`hidden shrink-0 border-l-2 sm:block sm:sticky ${STICKY_SLOT_TOP_CLASS} sm:self-start ${ACCENT_FRAME_PAD_LEFT_CLASS} ${ACCENT_FRAME_PAD_RIGHT_CLASS} ${ACCENT_FRAME_PAD_TOP_CLASS} ${accentEdgeClass(resolvedAccent)} ${accentGlowClass(resolvedAccent, "left")}`}
          data-testid="repo-shell-rail-slot"
        >
          {collapsed ? (
            <CollapsedRailIndicator title={title} engineKey={scopedKey} onExpand={toggle} />
          ) : (
            <div className="w-96">
              <ConversationRail engineKey={scopedKey} onCollapse={toggle} resolvedAccent={resolvedAccent} />
            </div>
          )}
        </div>
        <div
          style={accentVars(resolvedAccent)}
          className={`min-w-0 flex-1 border-r-2 ${ACCENT_FRAME_PAD_RIGHT_CLASS} ${ACCENT_FRAME_PAD_TOP_CLASS} ${ACCENT_FRAME_PAD_LEFT_CLASS} ${ACCENT_FRAME_PAD_BOTTOM_CLASS} ${accentEdgeClass(resolvedAccent)} ${accentGlowClass(resolvedAccent, "right")}`}
          data-testid="repo-shell-content-slot"
        >
          <Outlet />
        </div>
      </div>
      {drawerOpen && <RepoDrawer engineKey={scopedKey} onClose={closeDrawer} />}
    </div>
  );
}
