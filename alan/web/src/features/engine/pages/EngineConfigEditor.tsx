import { Navigate, useParams } from "react-router-dom";

import { useProjectEngineKey } from "../hooks/useProjectEngineKey";
import ErrorBanner from "../../../ui/ErrorBanner";

// engineKey is always "owner/repo" (EngineRepoIdentity.slug shape). Returns
// null on a malformed key instead of emitting a corrupted redirect target —
// this arrives from an async project fetch with no error boundary above it,
// so an explicit branch (rather than engineKeyParts()'s throw in
// EngineOpsPanel.tsx) keeps a bad value a graceful message, not a crash.
function repoConfigPath(engineKey: string): string | null {
  const idx = engineKey.indexOf("/");
  if (idx === -1) return null;
  const owner = engineKey.slice(0, idx);
  const repo = engineKey.slice(idx + 1);
  return `/repos/${owner}/${repo}/config`;
}

function EditorErrorNotice({ children }: Readonly<{ children: React.ReactNode }>) {
  return <ErrorBanner data-testid="engine-config-editor-error">{children}</ErrorBanner>;
}

// The repository-scoped screen (RepoEngineConfig) is now the sole engine
// configuration surface — this route stays only as a resolver so old
// bookmarks and links into a project's engine config keep landing somewhere
// correct, rather than 404ing or silently breaking.
export default function EngineConfigEditor() {
  const { projectId } = useParams<{ projectId: string }>();
  const projectState = useProjectEngineKey(projectId);

  if (projectId === undefined || projectState.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="engine-config-editor-loading">
        Loading…
      </p>
    );
  }

  if (projectState.status === "error") {
    return <EditorErrorNotice>{projectState.message}</EditorErrorNotice>;
  }

  if (projectState.engineKey == null) {
    return (
      <EditorErrorNotice>
        This project isn&apos;t bound to an engine, so there is no configuration screen to
        redirect to.
      </EditorErrorNotice>
    );
  }

  const configPath = repoConfigPath(projectState.engineKey);
  if (configPath === null) {
    return (
      <EditorErrorNotice>
        This project&apos;s engine reference is malformed, so there is no configuration screen to
        redirect to.
      </EditorErrorNotice>
    );
  }

  return <Navigate to={configPath} replace />;
}
