import type { EngineConfig, EngineFlowGraphPolicy, EngineSource } from "../../../api";
import SectionHeading from "../../../ui/SectionHeading";
import AgentCatalogueTile from "./AgentCatalogueTile";

const DL_CLASS = "space-y-1 text-sm";
const DT_CLASS = "text-xs font-medium text-text-muted";
const SUMMARY_CLASS = "cursor-pointer text-sm font-medium text-text-primary";

function joinOrDash(items: readonly string[], sep = ", "): string {
  return items.length > 0 ? items.join(sep) : "—";
}

function commandsToLines(commands: readonly (readonly string[])[]): string {
  return commands.length > 0 ? commands.map((c) => c.join(" ")).join("\n") : "—";
}

function VersionControlledPointer({ configPath }: Readonly<{ configPath: string | null }>) {
  if (configPath === null) {
    return (
      <p className="text-xs text-text-muted">
        This engine&apos;s structural configuration is version-controlled. To change it, edit the
        config file and merge the change through the normal review process.
      </p>
    );
  }
  return (
    <p className="text-xs text-text-muted">
      This engine&apos;s structural configuration is version-controlled at{" "}
      <code className="text-text-primary" data-testid="engine-config-readonly-config-path">
        {configPath}
      </code>. To change it, edit that file and merge the change through the normal review process.
    </p>
  );
}

function NotYetTrackedPointer({ exportCommand }: Readonly<{ exportCommand: string | null }>) {
  if (exportCommand === null) {
    return (
      <p className="text-xs text-text-muted">
        This engine&apos;s structural configuration is not yet tracked in version control. Export it
        to generate its config file, then edit that file and merge the change through review.
      </p>
    );
  }
  return (
    <p className="text-xs text-text-muted">
      This engine&apos;s structural configuration is not yet tracked in version control. Run{" "}
      <code className="text-text-primary" data-testid="engine-config-readonly-export-command">
        {exportCommand}
      </code>{" "}
      to generate its config file, then edit that file and merge the change through review.
    </p>
  );
}

function UnknownSourcePointer() {
  return (
    <p className="text-xs text-text-muted" data-testid="engine-config-readonly-unknown-source">
      Where this configuration is stored could not be determined.
    </p>
  );
}

function ExportPointer({ source }: Readonly<{ source: EngineSource }>) {
  return source.in_version_control ? (
    <VersionControlledPointer configPath={source.config_path} />
  ) : (
    <NotYetTrackedPointer exportCommand={source.export_command} />
  );
}

// Standalone so callers needing only the pointer notice (not the whole
// read-only body) can render it directly — read-only rendering only ever
// replaces an editable form once in_version_control is true; this notice is
// the "must not be a dead end" pointer for every other known state, including
// source === null (the sources fetch itself failed or returned no match).
export function EngineConfigSourceNotice({ source }: Readonly<{ source: EngineSource | null }>) {
  return (
    <div
      className="space-y-1 rounded-md border border-border bg-surface-elevated px-3 py-2"
      data-testid="engine-config-readonly-pointer"
    >
      {source === null ? <UnknownSourcePointer /> : <ExportPointer source={source} />}
    </div>
  );
}

function TeamListSection({
  title,
  testId,
  value,
}: Readonly<{ title: string; testId: string; value: Readonly<Record<string, readonly string[]>> }>) {
  const rows = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="space-y-2" data-testid={testId}>
      <SectionHeading level="h4">{title}</SectionHeading>
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">No entries.</p>
      ) : (
        <dl className={DL_CLASS}>
          {rows.map(([key, list]) => (
            <div key={key}>
              <dt className={DT_CLASS}>{key}</dt>
              <dd data-testid={`${testId}-${key}`}>{joinOrDash(list)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function ForgeSection({ forge }: Readonly<{ forge: EngineConfig["forge"] }>) {
  if (forge == null) {
    return (
      <div className="space-y-1" data-testid="engine-config-readonly-forge">
        <SectionHeading level="h4">Forge</SectionHeading>
        <p className="text-sm text-text-muted">No forge configured.</p>
      </div>
    );
  }
  const sortedChecks = [...forge.authoritative_checks!].sort((a, b) => a.localeCompare(b));
  return (
    <div className="space-y-2" data-testid="engine-config-readonly-forge">
      <SectionHeading level="h4">Forge</SectionHeading>
      <dl className={DL_CLASS}>
        <div>
          <dt className={DT_CLASS}>Repo</dt>
          <dd>
            {forge.repo.owner}/{forge.repo.repo}
          </dd>
        </div>
        <div>
          <dt className={DT_CLASS}>Credential env var name</dt>
          <dd>{forge.credential_env_name}</dd>
        </div>
        <div>
          <dt className={DT_CLASS}>Authoritative checks</dt>
          <dd>{joinOrDash(sortedChecks)}</dd>
        </div>
      </dl>
    </div>
  );
}

function CiSection({ ci }: Readonly<{ ci: EngineConfig["ci"] }>) {
  return (
    <div className="space-y-2" data-testid="engine-config-readonly-ci">
      <SectionHeading level="h4">CI</SectionHeading>
      <dl className={DL_CLASS}>
        <div>
          <dt className={DT_CLASS}>Main CI workflow</dt>
          <dd>{ci?.main_ci_workflow ?? "—"}</dd>
        </div>
        <div>
          <dt className={DT_CLASS}>Deploy workflow</dt>
          <dd>{ci?.deploy_workflow ?? "None"}</dd>
        </div>
      </dl>
    </div>
  );
}

const CODE_QUALITY_FIELDS: { key: keyof NonNullable<EngineConfig["code_quality"]>; label: string }[] = [
  { key: "project_key", label: "Sonar project key" },
  { key: "organization", label: "Sonar organization" },
  { key: "host_url", label: "Sonar host URL" },
  { key: "token_env_var", label: "Sonar token env var" },
];

function CodeQualitySection({ codeQuality }: Readonly<{ codeQuality: EngineConfig["code_quality"] }>) {
  return (
    <div className="space-y-2" data-testid="engine-config-readonly-code-quality">
      <SectionHeading level="h4">Code quality</SectionHeading>
      <dl className={DL_CLASS}>
        {CODE_QUALITY_FIELDS.map(({ key, label }) => (
          <div key={key}>
            <dt className={DT_CLASS}>{label}</dt>
            <dd>{codeQuality?.[key] ?? "—"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function FlowPolicyReadOnly({ policy }: Readonly<{ policy: EngineFlowGraphPolicy | null | undefined }>) {
  if (policy == null) {
    return (
      <p className="text-sm text-text-muted" data-testid="engine-config-flow-policy-empty">
        No flow policy configured.
      </p>
    );
  }
  return (
    <dl className={DL_CLASS} data-testid="engine-config-flow-policy">
      <div>
        <dt className={DT_CLASS}>Verifier teams</dt>
        <dd data-testid="engine-config-flow-policy-verifier-teams">
          {policy.verifier_teams.join(", ") || "—"}
        </dd>
      </div>
      <div>
        <dt className={DT_CLASS}>Merge teams</dt>
        <dd data-testid="engine-config-flow-policy-merge-teams">
          {policy.merge_teams.join(", ") || "—"}
        </dd>
      </div>
      <div>
        <dt className={DT_CLASS}>Terminal teams</dt>
        <dd data-testid="engine-config-flow-policy-terminal-teams">
          {policy.terminal_teams.join(", ") || "—"}
        </dd>
      </div>
      <div>
        <dt className={DT_CLASS}>Internal-only teams</dt>
        <dd data-testid="engine-config-flow-policy-internal-only-teams">
          {policy.internal_only_teams.join(", ") || "—"}
        </dd>
      </div>
      <div>
        <dt className={DT_CLASS}>Revisit cap</dt>
        <dd data-testid="engine-config-flow-policy-revisit-cap">{policy.revisit_cap}</dd>
      </div>
    </dl>
  );
}

export interface EngineConfigReadOnlyProps {
  engineKey: string;
  config: EngineConfig;
  source: EngineSource | null;
}

export default function EngineConfigReadOnly({
  engineKey,
  config,
  source,
}: Readonly<EngineConfigReadOnlyProps>) {
  return (
    <section className="space-y-4" data-testid="engine-config-readonly">
      <EngineConfigSourceNotice source={source} />

      <AgentCatalogueTile engineKey={engineKey} />

      <details className="space-y-1" data-testid="engine-config-readonly-pool-preset-disclosure">
        <summary className={SUMMARY_CLASS}>Worker pool &amp; preset</summary>
        <dl className={`${DL_CLASS} mt-3`}>
          <div>
            <dt className={DT_CLASS}>Worker pool size</dt>
            <dd data-testid="engine-config-readonly-pool-size">{config.pool_size ?? "Default"}</dd>
          </div>
          <div>
            <dt className={DT_CLASS}>Preset</dt>
            <dd data-testid="engine-config-readonly-preset">{config.preset ?? "None"}</dd>
          </div>
        </dl>
      </details>

      <details data-testid="engine-config-readonly-commands-disclosure">
        <summary className={SUMMARY_CLASS}>Gate &amp; warmup commands</summary>
        <div className="mt-3 space-y-4">
          <div className="space-y-1" data-testid="engine-config-readonly-gate-commands">
            <SectionHeading level="h4">Gate commands</SectionHeading>
            <pre className="whitespace-pre-wrap text-sm text-text-primary">
              {commandsToLines(config.gate_commands!)}
            </pre>
          </div>
          <div className="space-y-1" data-testid="engine-config-readonly-warmup-commands">
            <SectionHeading level="h4">Warmup commands</SectionHeading>
            <pre className="whitespace-pre-wrap text-sm text-text-primary">
              {commandsToLines(config.warmup_commands!)}
            </pre>
          </div>
        </div>
      </details>

      <details data-testid="engine-config-readonly-tools-disclosure">
        <summary className={SUMMARY_CLASS}>Tool allowlists</summary>
        <div className="mt-3 space-y-4">
          <div className="space-y-1" data-testid="engine-config-readonly-base-tools">
            <SectionHeading level="h4">Base tools</SectionHeading>
            <p className="text-sm text-text-primary">{joinOrDash(config.base_tools!)}</p>
          </div>
          <TeamListSection
            title="Per-team tool allowlists"
            testId="engine-config-readonly-team-tools"
            value={config.team_tools!}
          />
        </div>
      </details>

      <details data-testid="engine-config-readonly-roster-disclosure">
        <summary className={SUMMARY_CLASS}>Team roster &amp; flow policy</summary>
        <div className="mt-3 space-y-4">
          <TeamListSection
            title="Team roster"
            testId="engine-config-readonly-roster"
            value={config.transition_graph!}
          />
          <div className="space-y-1">
            <SectionHeading level="h4">Flow policy</SectionHeading>
            <FlowPolicyReadOnly policy={config.flow_policy} />
          </div>
        </div>
      </details>

      <details data-testid="engine-config-readonly-forge-disclosure">
        <summary className={SUMMARY_CLASS}>Forge, CI &amp; code quality</summary>
        <div className="mt-3 space-y-4">
          <ForgeSection forge={config.forge} />
          <CiSection ci={config.ci} />
          <CodeQualitySection codeQuality={config.code_quality} />
        </div>
      </details>
    </section>
  );
}
