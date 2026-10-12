import { useCallback, useState } from "react";
import type { FormEvent } from "react";

import { api, type SecretRecordResponse } from "../api";
import { errorMessageOf } from "../lib/errorMessage";
import { classifyMutationError, type MutationBlock } from "../lib/connectFlowErrors";
import { parseRepoIdentity, repoIdentityErrorText, segmentCharsetErrorText } from "../lib/repoUrlIdentity";
import { useChangeRefetch } from "../api/ServerEventsProvider";
import { useAction } from "../ui/useAction";
import Button from "./Button";
import ConfirmDestructiveDialog from "./ConfirmDestructiveDialog";
import MutationErrorNotice from "./wizard/MutationErrorNotice";

const PURPOSES = ["transport", "reviewer"] as const;
type SecretPurpose = (typeof PURPOSES)[number];

const INPUT_CLASSES =
  "min-h-11 w-full rounded-md border border-border bg-surface px-3 py-1.5 text-sm " +
  "text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";
const LABEL_CLASSES = "mb-1 block text-xs font-medium text-text-primary";

type ListState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; secrets: SecretRecordResponse[] };

// Every SecretRecordResponse.name is server-produced as record_name(engine,
// purpose) = f"{engine}:{purpose}" (alan.secrets_store), so it always
// contains a colon — split on the last one rather than guard a case that
// can't occur.
function splitRecordName(name: string): [engine: string, purpose: string] {
  const idx = name.lastIndexOf(":");
  return [name.slice(0, idx), name.slice(idx + 1)];
}

function useSecretsList(): { state: ListState; refetch: () => void } {
  const [state, setState] = useState<ListState>({ status: "loading" });
  const load = useCallback((): void => {
    setState((prev) => (prev.status === "ready" ? prev : { status: "loading" }));
    api.listSecrets().then(
      (secrets) => {
        setState({ status: "ready", secrets });
      },
      (err: unknown) => {
        setState({ status: "error", message: errorMessageOf(err) });
      },
    );
  }, []);
  useChangeRefetch("engines", load, true);
  return { state, refetch: load };
}

function SecretRow({
  secret,
  onDeleteRequested,
}: Readonly<{
  secret: SecretRecordResponse;
  onDeleteRequested: (secret: SecretRecordResponse) => void;
}>) {
  return (
    <li
      data-testid={`credential-row-${secret.name}`}
      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface px-3 py-2"
    >
      <div className="space-y-0.5">
        <p className="text-sm text-text-primary" data-testid={`credential-row-${secret.name}-name`}>
          {secret.name}
        </p>
        <p className="text-xs text-text-muted">
          fingerprint {secret.fingerprint}
          {secret.last_verified != null && ` · verified ${secret.last_verified}`}
          {secret.expires_at != null && ` · expires ${secret.expires_at}`}
        </p>
      </div>
      <Button
        variant="destroy"
        data-testid={`credential-row-${secret.name}-delete`}
        onClick={() => {
          onDeleteRequested(secret);
        }}
      >
        Delete
      </Button>
    </li>
  );
}

function CredentialForm({ onCreated }: Readonly<{ onCreated: () => void }>) {
  const [engine, setEngine] = useState("");
  const [purpose, setPurpose] = useState<SecretPurpose>("transport");
  const [value, setValue] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const { busy: submitting, run } = useAction();
  const [block, setBlock] = useState<MutationBlock | null>(null);

  const parsed = parseRepoIdentity(engine);
  const previewName = parsed.ok ? `${parsed.owner}/${parsed.repo}:${purpose}` : `owner/repo:${purpose}`;

  const handleSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!parsed.ok) {
      const { reason } = parsed;
      if (reason !== "empty") {
        setBlock({ kind: "validation", detail: repoIdentityErrorText(reason) });
      }
      return;
    }
    // Re-validate before the request: a URL/slug can parse cleanly and still
    // carry an owner or repo outside the charset api.ts's assertSafeSegment
    // enforces server-side — catch it here rather than storing it and
    // failing the round trip.
    const segmentError = segmentCharsetErrorText(parsed.owner, parsed.repo);
    if (segmentError !== null) {
      setBlock({ kind: "validation", detail: segmentError });
      return;
    }
    const payload = {
      engine: `${parsed.owner}/${parsed.repo}`,
      purpose,
      value,
      expires_at: expiresAt.trim() === "" ? null : expiresAt.trim(),
    };
    // Cleared before the request resolves — no submitted value survives even
    // transiently in this component's state once the round trip is in flight.
    setValue("");
    setBlock(null);
    run(() => api.createSecret(payload)).then(
      () => {
        setEngine("");
        setExpiresAt("");
        onCreated();
      },
      (err: unknown) => {
        setBlock(classifyMutationError(err, "secret"));
      },
    );
  };

  return (
    <form className="space-y-2" onSubmit={handleSubmit} data-testid="credentials-form">
      <div>
        <label htmlFor="credential-engine" className={LABEL_CLASSES}>
          Repository
        </label>
        <input
          id="credential-engine"
          value={engine}
          onChange={(e) => {
            setEngine(e.target.value);
          }}
          placeholder="owner/repo or https://github.com/owner/repo"
          data-testid="credential-engine-input"
          className={INPUT_CLASSES}
        />
        <p
          role="status"
          aria-live="polite"
          className="mt-1 text-xs text-text-muted"
          data-testid="credential-preview"
        >
          Will be stored as {previewName}
        </p>
      </div>
      <div>
        <label htmlFor="credential-purpose" className={LABEL_CLASSES}>
          Purpose
        </label>
        <select
          id="credential-purpose"
          value={purpose}
          onChange={(e) => {
            setPurpose(e.target.value as SecretPurpose);
          }}
          data-testid="credential-purpose-select"
          className={INPUT_CLASSES}
        >
          {PURPOSES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="credential-value" className={LABEL_CLASSES}>
          Token value
        </label>
        <input
          id="credential-value"
          type="password"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
          }}
          data-testid="credential-value-input"
          className={INPUT_CLASSES}
        />
      </div>
      <div>
        <label htmlFor="credential-expires" className={LABEL_CLASSES}>
          Expires (optional, YYYY-MM-DD)
        </label>
        <input
          id="credential-expires"
          value={expiresAt}
          onChange={(e) => {
            setExpiresAt(e.target.value);
          }}
          data-testid="credential-expires-input"
          className={INPUT_CLASSES}
        />
      </div>
      <Button
        variant="create"
        type="submit"
        data-testid="credential-submit"
        disabled={submitting || engine.trim() === "" || value === ""}
      >
        {submitting ? "Adding…" : "Add credential"}
      </Button>
      {block !== null && <MutationErrorNotice block={block} testId="credential-form-error" />}
    </form>
  );
}

function CredentialsListView({
  secrets,
  onChanged,
}: Readonly<{ secrets: SecretRecordResponse[]; onChanged: () => void }>) {
  const [pendingDelete, setPendingDelete] = useState<SecretRecordResponse | null>(null);
  const [deleteBlock, setDeleteBlock] = useState<MutationBlock | null>(null);

  const confirmDelete = (secret: SecretRecordResponse): void => {
    const [engine, purpose] = splitRecordName(secret.name);
    setPendingDelete(null);
    setDeleteBlock(null);
    api.deleteSecret({ engine, purpose }).then(onChanged, (err: unknown) => {
      setDeleteBlock(classifyMutationError(err, "secret"));
    });
  };

  return (
    <div className="space-y-3">
      {secrets.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="credentials-empty">
          No credentials yet.
        </p>
      ) : (
        <ul className="max-h-64 space-y-2 overflow-y-auto" data-testid="credentials-list">
          {secrets.map((secret) => (
            <SecretRow
              key={secret.name}
              secret={secret}
              onDeleteRequested={(s) => {
                setPendingDelete(s);
              }}
            />
          ))}
        </ul>
      )}
      {deleteBlock !== null && (
        <MutationErrorNotice block={deleteBlock} testId="credential-delete-error" />
      )}
      <CredentialForm onCreated={onChanged} />
      {pendingDelete !== null && (
        <ConfirmDestructiveDialog
          title="Delete credential"
          body={`Delete the credential record "${pendingDelete.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          onConfirm={() => {
            confirmDelete(pendingDelete);
          }}
          onCancel={() => {
            setPendingDelete(null);
          }}
          testId="credential-delete-confirm"
        />
      )}
    </div>
  );
}

export default function CredentialsSection() {
  const { state, refetch } = useSecretsList();
  return (
    <section className="space-y-3" data-testid="credentials-section">
      <h3 className="text-sm font-medium text-text-primary">Credentials</h3>
      {state.status === "loading" && (
        <p className="text-sm text-text-muted" data-testid="credentials-loading">
          Loading…
        </p>
      )}
      {state.status === "error" && (
        <p role="alert" className="text-sm text-btn-destroy" data-testid="credentials-error">
          {state.message}
        </p>
      )}
      {state.status === "ready" && (
        <CredentialsListView secrets={state.secrets} onChanged={refetch} />
      )}
    </section>
  );
}
