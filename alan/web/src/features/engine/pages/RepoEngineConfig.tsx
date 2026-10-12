import { useParams } from "react-router-dom";

import type { EngineSource } from "../../../api";
import AccentPicker from "../components/AccentPicker";
import Button from "../../../components/Button";
import EngineConfigReadOnly from "../components/EngineConfigReadOnly";
import { DependencySweepTile, PostureTile } from "../../../components/EngineOpsPanel";
import { blobUrl } from "../../../lib/githubLinks";
import { useEngineConfigDoc, type UseEngineConfigDocResult } from "../hooks/useEngineConfigDoc";
import { useDefaultEngineKey, useEngines } from "../../../hooks/useEngines";

function HiddenRepoNotice({ engineKey }: Readonly<{ engineKey: string }>) {
  const { allEngines } = useEngines();
  const isHidden = allEngines.some(
    (e) => `${e.repo.owner}/${e.repo.repo}` === engineKey && e.hidden === true,
  );
  if (!isHidden) return null;
  return (
    <output data-testid="repo-engine-config-hidden-notice" className="text-xs text-text-muted">
      This repository is hidden — reachable here by direct link. Restore it from the hidden
      repositories list on Home to resume automation.
    </output>
  );
}

function PostureCeilingNote({ source }: Readonly<{ source: EngineSource | null }>) {
  const defaultEngineKey = useDefaultEngineKey();
  if (source === null || !source.in_version_control || source.posture_ceiling === "autonomous") {
    return null;
  }
  return (
    <p className="text-xs text-text-muted" data-testid="repo-engine-config-posture-ceiling-note">
      Posture is capped at <strong>{source.posture_ceiling}</strong> by the reviewed config file.
      {source.config_path !== null && (
        <>
          {" "}To raise it, edit{" "}
          {defaultEngineKey === null ? (
            <code>{source.config_path}</code>
          ) : (
            <a
              href={blobUrl(defaultEngineKey, source.config_path)}
              target="_blank"
              rel="noreferrer"
              className="text-btn-action underline"
            >
              {source.config_path}
            </a>
          )}{" "}
          and merge the change through review.
        </>
      )}
    </p>
  );
}

interface LiveSectionsProps {
  visible: boolean;
  engineKey: string;
  source: EngineSource | null;
}

function LiveSections({ visible, engineKey, source }: Readonly<LiveSectionsProps>) {
  if (!visible) return null;
  return (
    <>
      <section className="space-y-2" data-testid="repo-engine-config-live-controls">
        <h2 className="text-sm font-medium text-text-primary">Live controls</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <PostureTile engineKey={engineKey} />
            <PostureCeilingNote source={source} />
          </div>
          <DependencySweepTile engineKey={engineKey} />
        </div>
      </section>

      <section data-testid="repo-engine-config-accent">
        <AccentPicker key={engineKey} engineKey={engineKey} />
      </section>
    </>
  );
}

interface BodyProps {
  owner: string;
  repo: string;
  docState: UseEngineConfigDocResult;
}

function RepoEngineConfigBody({ owner, repo, docState }: Readonly<BodyProps>) {
  if (docState.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="repo-engine-config-loading">
        Loading…
      </p>
    );
  }

  if (docState.status === "error") {
    return (
      <div className="space-y-2">
        <p role="alert" data-testid="repo-engine-config-error" className="text-sm text-btn-destroy">
          {docState.message}
        </p>
        <Button variant="ghost" data-testid="repo-engine-config-retry" onClick={docState.refetch}>
          Retry
        </Button>
      </div>
    );
  }

  if (docState.status === "empty") {
    return (
      <div
        className="space-y-3 rounded-lg border border-dashed border-border bg-surface-elevated/30 p-6"
        data-testid="repo-engine-config-empty"
      >
        <p className="text-sm text-text-muted">
          This repository has no engine configuration yet — connect it to register one.
        </p>
        <Button
          variant="create"
          linkTo={`/connect/${owner}/${repo}`}
          data-testid="repo-engine-config-connect-cta"
        >
          Connect repository
        </Button>
      </div>
    );
  }

  return (
    <EngineConfigReadOnly
      engineKey={`${owner}/${repo}`}
      config={docState.config}
      source={docState.source}
    />
  );
}

export default function RepoEngineConfig() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  const engineKey = owner !== undefined && repo !== undefined ? `${owner}/${repo}` : undefined;
  const docState = useEngineConfigDoc(engineKey);
  const source = docState.status === "ready" ? docState.source : null;

  if (engineKey === undefined || owner === undefined || repo === undefined) {
    return (
      <p className="text-sm text-text-muted" data-testid="repo-engine-config-loading">
        Loading…
      </p>
    );
  }

  return (
    <div className="space-y-6" data-testid="repo-engine-config">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold text-text-primary">Engine configuration</h1>
        <p className="text-sm text-text-primary" data-testid="repo-engine-config-identity">
          {engineKey}
        </p>
        <HiddenRepoNotice engineKey={engineKey} />
      </div>

      <LiveSections visible={docState.status !== "empty"} engineKey={engineKey} source={source} />

      <RepoEngineConfigBody owner={owner} repo={repo} docState={docState} />
    </div>
  );
}
