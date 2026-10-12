import type { AgentKind } from "../../../api";
import Input from "../../../ui/Input";
import {
  AGENT_KINDS,
  AGENT_KIND_LABEL,
  type AgentFilterOptions,
  type AgentFilters,
} from "../../../lib/agentSearch";

export interface AgentSearchBarProps {
  readonly query: string;
  readonly filters: AgentFilters;
  readonly options: AgentFilterOptions;
  readonly shown: number;
  readonly total: number;
  readonly onQueryChange: (query: string) => void;
  readonly onFilterChange: (key: keyof AgentFilters, value: string | null) => void;
}

interface SelectFieldProps {
  readonly widthClass: string;
  readonly label: string;
  readonly allLabel: string;
  readonly value: string | null;
  readonly choices: readonly { readonly value: string; readonly label: string }[];
  readonly testId: string;
  readonly onChange: (value: string | null) => void;
}

const FIELD_LABEL = "flex min-w-0 flex-col gap-1 text-xs font-medium text-text-muted";

function SelectField({
  widthClass,
  label,
  allLabel,
  value,
  choices,
  testId,
  onChange,
}: Readonly<SelectFieldProps>) {
  return (
    <div className={`${FIELD_LABEL} ${widthClass}`}>
      <label htmlFor={testId}>{label}</label>
      <Input
        as="select"
        tapTarget
        focusRing
        id={testId}
        value={value ?? ""}
        data-testid={testId}
        onChange={(event) => {
          onChange(event.target.value === "" ? null : event.target.value);
        }}
      >
        <option value="">{allLabel}</option>
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </Input>
    </div>
  );
}

function asChoices(values: readonly string[]): { value: string; label: string }[] {
  return values.map((value) => ({ value, label: value }));
}

const KIND_CHOICES = AGENT_KINDS.map((kind: AgentKind) => ({ value: kind, label: AGENT_KIND_LABEL[kind] }));

export default function AgentSearchBar({
  query,
  filters,
  options,
  shown,
  total,
  onQueryChange,
  onFilterChange,
}: Readonly<AgentSearchBarProps>) {
  return (
    <div role="search" aria-label="Search agents" className="space-y-3" data-testid="agent-search">
      <div className={FIELD_LABEL}>
        <label htmlFor="agent-search-input">Search agent names, models, efforts and prompt text</label>
        <Input
          as="input"
          type="search"
          tapTarget
          focusRing
          id="agent-search-input"
          placeholder="Search…"
          placeholderStyle
          value={query}
          data-testid="agent-search-input"
          onChange={(event) => {
            onQueryChange(event.target.value);
          }}
        />
      </div>
      <div className="flex flex-wrap gap-3">
        <SelectField
          widthClass="w-full sm:w-64"
          label="Model"
          allLabel="All models"
          value={filters.model}
          choices={asChoices(options.models)}
          testId="agent-filter-model"
          onChange={(value) => {
            onFilterChange("model", value);
          }}
        />
        <SelectField
          widthClass="w-full sm:w-48"
          label="Effort"
          allLabel="All efforts"
          value={filters.effort}
          choices={asChoices(options.efforts)}
          testId="agent-filter-effort"
          onChange={(value) => {
            onFilterChange("effort", value);
          }}
        />
        <SelectField
          widthClass="w-full sm:w-56"
          label="Kind"
          allLabel="All kinds"
          value={filters.kind}
          choices={KIND_CHOICES}
          testId="agent-filter-kind"
          onChange={(value) => {
            onFilterChange("kind", value);
          }}
        />
      </div>
      <p
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="text-sm text-text-muted"
        data-testid="agent-search-count"
      >
        {`Showing ${String(shown)} of ${String(total)} agents`}
      </p>
    </div>
  );
}
