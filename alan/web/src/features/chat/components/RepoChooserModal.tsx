import { useNavigate } from "react-router-dom";

import type { RepoRegistryEntry } from "../../../lib/fold";
import ChooserRow from "../../../components/ChooserRow";
import ListChooserModal from "../../../components/ListChooserModal";

const DEFAULT_DESCRIPTION = "Choose which repository this conversation is for.";

interface Props {
  repos: readonly RepoRegistryEntry[];
  description?: string | undefined;
  onSelect: (engineKey: string) => void;
  onClose: () => void;
}

export default function RepoChooserModal({
  repos,
  description,
  onSelect,
  onClose,
}: Readonly<Props>) {
  return (
    <ListChooserModal
      ariaLabel="Choose a repository"
      testId="repo-chooser-modal"
      title="Choose a repository"
      description={description ?? DEFAULT_DESCRIPTION}
      items={repos}
      getItemKey={(r) => r.key}
      getItemTestId={(r) => `repo-option-${r.key}`}
      renderItem={(r) => <ChooserRow label={r.title} description={r.key} />}
      onSelect={(r) => onSelect(r.key)}
      onClose={onClose}
    />
  );
}

interface GateProps {
  show: boolean;
  repos: readonly RepoRegistryEntry[];
  description?: string | undefined;
  onSelect: (engineKey: string) => void;
}

// Shared by BuildChat and ExploreChat: both gate their global entry point on the
// same repo chooser, closing back to home when the person cancels the pick.
export function RepoChooserGate({ show, repos, description, onSelect }: Readonly<GateProps>) {
  const navigate = useNavigate();
  if (!show) return null;
  return (
    <RepoChooserModal
      repos={repos}
      description={description}
      onSelect={onSelect}
      onClose={() => {
        void navigate("/");
      }}
    />
  );
}
