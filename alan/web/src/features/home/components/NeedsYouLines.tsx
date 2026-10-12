import { Link } from "react-router-dom";

import { useSettingsUi } from "../../../shell/SettingsUiProvider";
import type { ResumeEntry } from "../hooks/useConnectStates";

// The ratified body-link recipe (`text-btn-action underline`) plus an
// explicit tap-target obligation — these lines can be a full sentence, so
// they must wrap rather than overflow at 390px rather than truncate.
const LINE_CLASS = "inline-flex min-h-11 items-center text-btn-action underline";

export interface NeedsYouLinesProps {
  readonly hostSetupMessage: string | null;
  readonly resumes: readonly ResumeEntry[];
}

export default function NeedsYouLines({
  hostSetupMessage,
  resumes,
}: Readonly<NeedsYouLinesProps>) {
  const { open } = useSettingsUi();
  if (hostSetupMessage === null && resumes.length === 0) return null;
  return (
    <div className="flex flex-col items-start gap-1" data-testid="needs-you-lines">
      {hostSetupMessage !== null && (
        <button type="button" data-testid="needs-you-host-setup" onClick={open} className={LINE_CLASS}>
          {hostSetupMessage}
        </button>
      )}
      {resumes.map((resume) => (
        <Link
          key={`${resume.owner}/${resume.repo}`}
          to={`/connect/${encodeURIComponent(resume.owner)}/${encodeURIComponent(resume.repo)}`}
          data-testid={`needs-you-resume-${resume.owner}-${resume.repo}`}
          className={LINE_CLASS}
        >
          {`Finish setup of ${resume.owner}/${resume.repo} — step ${String(resume.step)} →`}
        </Link>
      ))}
    </div>
  );
}
