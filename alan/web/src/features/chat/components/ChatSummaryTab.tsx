import { useState, type ReactNode } from "react";

import type { CollisionInfo } from "../hooks/useCollisionCheck";
import CollisionBox from "./CollisionBox";
import EditableBrief from "./EditableBrief";
import MarkdownSourceToggle from "../../../components/MarkdownSourceToggle";

export type SummaryMode = "edit" | "preview";

interface ChatSummaryTabProps {
  brief: { content: string } | null;
  onEdit: (v: string) => void;
  renderProposal: (liveBrief: string) => ReactNode;
  collisionInfo: CollisionInfo;
  onRerunCollision: (brief: string) => void;
  mode: SummaryMode;
  onModeChange: (mode: SummaryMode) => void;
  onLiveBriefChange?: (v: string) => void;
}

export default function ChatSummaryTab({
  brief,
  onEdit,
  renderProposal,
  collisionInfo,
  onRerunCollision,
  mode,
  onModeChange,
  onLiveBriefChange,
}: Readonly<ChatSummaryTabProps>) {
  const [liveBrief, setLiveBrief] = useState(brief?.content ?? "");
  const isDirty = brief !== null && liveBrief !== brief.content;
  // Only true immediately after the toggle click below drove a preview->edit
  // transition — never on a fresh mount (including the remount this component
  // gets via its parent's `key={briefKey}` when a blur-triggered PUT resolves
  // while mode is already "edit"). That's what stops autofocus from
  // reactivating on every such remount instead of only the deliberate click.
  const [justEnteredEdit, setJustEnteredEdit] = useState(false);

  const handleModeChange = (): void => {
    if (isDirty) onEdit(liveBrief);
    const next = mode === "edit" ? "preview" : "edit";
    setJustEnteredEdit(next === "edit");
    onModeChange(next);
  };

  return (
    <div className="flex flex-col gap-3">
      <MarkdownSourceToggle
        showSource={mode === "edit"}
        onToggle={handleModeChange}
        testId="summary-source-toggle"
        labelSet="edit"
      />
      <EditableBrief
        brief={brief}
        value={liveBrief}
        onChange={(v) => {
          setLiveBrief(v);
          onLiveBriefChange?.(v);
        }}
        onEdit={onEdit}
        mode={mode}
        rows={12}
        focusOnEdit={justEnteredEdit}
      />
      {brief !== null && (
        <CollisionBox info={collisionInfo} onRerun={() => onRerunCollision(liveBrief)} />
      )}
      {renderProposal(liveBrief)}
    </div>
  );
}
