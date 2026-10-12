import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import type { ConsultantInfo } from "../api";
import { useConsultants } from "../hooks/useConsultants";
import ErrorBanner from "../ui/ErrorBanner";
import ChooserRow from "./ChooserRow";
import ListChooserModal from "./ListChooserModal";

export interface ConsultantChoices {
  items: readonly ConsultantInfo[];
  notice: ReactNode | undefined;
}

export function useConsultantChoices(): ConsultantChoices {
  const { consultants, loadFailed } = useConsultants();
  if (loadFailed) {
    return {
      items: consultants,
      notice: (
        <ErrorBanner data-testid="consultants-load-error">
          Could not load consultants.
        </ErrorBanner>
      ),
    };
  }
  return { items: consultants, notice: undefined };
}

interface Props {
  onClose: () => void;
  repoKey: string | null;
}

export default function ConsultantModal({ onClose, repoKey }: Readonly<Props>) {
  const navigate = useNavigate();
  const { items, notice } = useConsultantChoices();

  const handleSelect = (entry: ConsultantInfo): void => {
    const target =
      repoKey === null
        ? `/explore/${encodeURIComponent(entry.key)}`
        : `/repos/${repoKey}/explore/${encodeURIComponent(entry.key)}`;
    void navigate(target);
    onClose();
  };

  return (
    <ListChooserModal
      ariaLabel="Choose a consultant"
      testId="consultant-modal"
      title="Choose a consultant"
      description="Choosing a consultant opens a conversation with them; nothing else starts automatically."
      notice={notice}
      items={items}
      getItemKey={(c) => c.key}
      getItemTestId={(c) => `consultant-${c.key}`}
      renderItem={(c) => <ChooserRow label={c.label} description={c.description} />}
      onSelect={handleSelect}
      onClose={onClose}
    />
  );
}
