import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import {
  api,
  type ControlTokenStatus,
  type DependencySweepStatus,
  type EngineConfig,
  type EnginePoolState,
  type EnginePreflightResult,
  type EngineSummary,
  type OnboardingReadiness,
  type RecurringStatus,
  type RepoProvisionStatus,
  type RepoStatus,
  type RunSummary,
  type WorkspaceDetail,
} from "../api";
import { fetchEngineListing } from "./engines";
import { getOperatorStatus } from "./operator";
import { queryClient } from "./queryClient";

const HEALTH_CONTEXT_KEY = ["health", "context"] as const;
const OPERATOR_STATUS_KEY = ["operator", "status"] as const;
const OPERATOR_WORKSPACE_TARGETS_KEY = ["operator", "workspace-targets"] as const;
const HEALTH_DIGEST_KEY = ["health", "digest"] as const;
const CONTROL_TOKEN_STATUS_KEY = ["control-token", "status"] as const;
const ENGINES_LIST_KEY = ["engines", "list"] as const;
const ENGINES_DEFAULT_KEY = ["engines", "default"] as const;
const CONSULTANTS_LIST_KEY = ["consultants", "list"] as const;
const PAUSED_POLL_MS = 5000;
const REPO_STATUS_POLL_MS = 60_000;
export const CONTROL_TOKEN_STATUS_POLL_MS = 30_000;

queryClient.setQueryDefaults(ENGINES_DEFAULT_KEY, { gcTime: Infinity });

interface EngineResourcePayloads {
  engineConfig: EngineConfig;
  pool: EnginePoolState;
  preflight: EnginePreflightResult | null;
  depSweep: DependencySweepStatus;
  onboarding: OnboardingReadiness;
  repoStatus: RepoStatus;
  recurringStatus: RecurringStatus;
}

export type EngineResource = keyof EngineResourcePayloads;
export type EngineResourcePayload<K extends EngineResource> = EngineResourcePayloads[K];

export interface EngineQueryView<T> {
  readonly data: T | undefined;
  readonly isError: boolean;
  readonly error: unknown;
  readonly isFetching: boolean;
  readonly isPlaceholderData: boolean;
}

export interface DocQueryView<T> {
  readonly data: T | undefined;
  readonly isError: boolean;
  readonly error: unknown;
}

interface EngineResourceSpec<T> {
  readonly fetch: (engineKey: string) => Promise<T>;
  readonly pollMs?: number;
}

const ENGINE_RESOURCES: { [K in EngineResource]: EngineResourceSpec<EngineResourcePayloads[K]> } = {
  engineConfig: { fetch: (engineKey) => api.getEngine(engineKey) },
  pool: { fetch: (engineKey) => api.getEnginePool(engineKey) },
  preflight: { fetch: (engineKey) => api.getEnginePreflight(engineKey) },
  depSweep: { fetch: (engineKey) => api.getEngineDependencySweep(engineKey) },
  onboarding: { fetch: (engineKey) => api.getEngineOnboarding(engineKey) },
  repoStatus: { fetch: (engineKey) => api.getRepoStatus(engineKey), pollMs: REPO_STATUS_POLL_MS },
  recurringStatus: {
    fetch: (engineKey) => api.getRecurringStatus(engineKey),
    pollMs: REPO_STATUS_POLL_MS,
  },
};

function engineKeyOf(resource: EngineResource, engineKey: string | null) {
  return ["engine", resource, engineKey] as const;
}

function docKeyOf(namespace: string, key: string | undefined) {
  return ["doc", namespace, key ?? null] as const;
}

function chatSessionKeyOf(family: string, sessionId: string | null) {
  return ["chat-session", family, sessionId] as const;
}

const NO_PLACEHOLDER = () => undefined;

function workspaceKeyOf(taskId: string | undefined) {
  return ["workspace", taskId ?? null] as const;
}

function workspaceRepoKeyOf(taskId: string | undefined) {
  return ["workspace-repo", taskId ?? null] as const;
}

function workspaceInvocationsPrefixOf(taskId: string | undefined) {
  return ["workspace-invocations", taskId ?? null] as const;
}

function useInvalidateExact(queryKey: readonly unknown[], cancelRefetch = false): () => void {
  const client = useQueryClient(queryClient);
  return useCallback(() => {
    void client.invalidateQueries({ queryKey, exact: true }, { cancelRefetch });
  }, [client, queryKey, cancelRefetch]);
}

function useResetExact(queryKey: readonly unknown[]): () => void {
  const client = useQueryClient(queryClient);
  return useCallback(() => {
    void client.resetQueries({ queryKey, exact: true });
  }, [client, queryKey]);
}

export function useHealthQuery() {
  return useQuery(
    {
      queryKey: HEALTH_CONTEXT_KEY,
      queryFn: () => api.getHealth(),
      refetchInterval: (query) =>
        query.state.status === "error" || !query.state.data?.queue_paused ? false : PAUSED_POLL_MS,
      refetchIntervalInBackground: true,
    },
    queryClient,
  );
}

export function useOperatorStatusQuery() {
  return useQuery(
    {
      queryKey: OPERATOR_STATUS_KEY,
      queryFn: () => getOperatorStatus(),
      staleTime: 0,
      refetchOnMount: "always",
    },
    queryClient,
  );
}

export function useInvalidateOperatorStatus(): () => void {
  return useInvalidateExact(OPERATOR_STATUS_KEY);
}

export function useOperatorWorkspaceTargetsQuery(enabled: boolean) {
  return useQuery(
    {
      queryKey: OPERATOR_WORKSPACE_TARGETS_KEY,
      queryFn: () => api.listWorkspaces({ includeTriage: true }),
      enabled,
      staleTime: 0,
      refetchOnMount: "always",
      gcTime: 0,
    },
    queryClient,
  );
}

export function useHealthDigestQuery(enabled: boolean) {
  return useQuery(
    {
      queryKey: HEALTH_DIGEST_KEY,
      queryFn: () => api.getHealthDigest(),
      enabled,
    },
    queryClient,
  );
}

export function useEnginesListQuery() {
  return useQuery(
    {
      queryKey: ENGINES_LIST_KEY,
      queryFn: async (): Promise<EngineSummary[]> => {
        const listing = await fetchEngineListing();
        queryClient.setQueryData(ENGINES_DEFAULT_KEY, listing.default_engine_key ?? null);
        return listing.engines;
      },
      structuralSharing: false,
    },
    queryClient,
  );
}

export function useListedDefaultEngineKeyQuery(): string | null | undefined {
  return useQuery<string | null>(
    { queryKey: ENGINES_DEFAULT_KEY, queryFn: skipToken },
    queryClient,
  ).data;
}

export function useConsultantsQuery() {
  return useQuery(
    {
      queryKey: CONSULTANTS_LIST_KEY,
      queryFn: () => api.fetchConsultants(),
    },
    queryClient,
  );
}

export function useControlTokenStatusQuery() {
  return useQuery(
    {
      queryKey: CONTROL_TOKEN_STATUS_KEY,
      queryFn: () => api.getControlTokenStatus(),
      staleTime: 0,
      refetchOnMount: "always",
      refetchInterval: CONTROL_TOKEN_STATUS_POLL_MS,
      refetchIntervalInBackground: true,
      gcTime: 0,
    },
    queryClient,
  );
}

export function useSetControlTokenStatus(): (next: ControlTokenStatus) => void {
  const client = useQueryClient(queryClient);
  return useCallback(
    (next: ControlTokenStatus) => {
      if (client.getQueryState(CONTROL_TOKEN_STATUS_KEY) === undefined) {
        return;
      }
      client.setQueryData<ControlTokenStatus>(CONTROL_TOKEN_STATUS_KEY, next);
    },
    [client],
  );
}

export function useInvalidateHealthContext(): () => void {
  return useInvalidateExact(HEALTH_CONTEXT_KEY);
}

export function useInvalidateHealthDigest(): () => void {
  return useInvalidateExact(HEALTH_DIGEST_KEY);
}

export function useEngineResourceQuery<K extends EngineResource>(
  resource: K,
  engineKey: string | null,
): EngineQueryView<EngineResourcePayload<K>> {
  const spec = ENGINE_RESOURCES[resource];
  const query = useQuery(
    {
      queryKey: engineKeyOf(resource, engineKey),
      queryFn: () => spec.fetch(engineKey!),
      enabled: engineKey !== null,
      staleTime: 0,
      refetchOnMount: "always",
      refetchInterval: spec.pollMs ?? false,
      refetchIntervalInBackground: true,
    },
    queryClient,
  );
  return {
    data: query.data,
    isError: query.isError,
    error: query.error,
    isFetching: query.isFetching,
    isPlaceholderData: query.isPlaceholderData,
  };
}

export function useInvalidateEnginesList(): () => void {
  return useInvalidateExact(ENGINES_LIST_KEY);
}

export function useInvalidateEngineResource(
  resource: EngineResource,
  engineKey: string | null,
): () => void {
  const queryKey = useMemo(() => engineKeyOf(resource, engineKey), [resource, engineKey]);
  return useInvalidateExact(queryKey);
}

export interface DocQueryOptions {
  readonly pollMs?: number | undefined;
}

export function useDocQuery<T>(
  namespace: string,
  key: string | undefined,
  fetch: (key: string) => Promise<T>,
  options: DocQueryOptions = {},
): DocQueryView<T> {
  const query = useQuery(
    {
      queryKey: docKeyOf(namespace, key),
      queryFn: () => fetch(key!),
      enabled: key !== undefined,
      placeholderData: NO_PLACEHOLDER,
      gcTime: 0,
      staleTime: 0,
      refetchOnMount: "always",
      refetchInterval: options.pollMs ?? false,
      refetchIntervalInBackground: false,
    },
    queryClient,
  );
  return { data: query.data, isError: query.isError, error: query.error };
}

export function useRefetchDoc(namespace: string, key: string | undefined): () => void {
  const queryKey = useMemo(() => docKeyOf(namespace, key), [namespace, key]);
  return useInvalidateExact(queryKey, true);
}

export function useSetDocData<T>(
  namespace: string,
  key: string | undefined,
): (update: (previous: T | undefined) => T | undefined) => void {
  const client = useQueryClient(queryClient);
  const queryKey = useMemo(() => docKeyOf(namespace, key), [namespace, key]);
  return useCallback(
    (update) => {
      if (key === undefined || client.getQueryData(queryKey) === undefined) return;
      client.setQueryData<T>(queryKey, update);
    },
    [client, key, queryKey],
  );
}

export function useResetDoc(namespace: string, key: string | undefined): () => void {
  const queryKey = useMemo(() => docKeyOf(namespace, key), [namespace, key]);
  return useResetExact(queryKey);
}

interface ChatSessionWire {
  readonly run_summary: RunSummary;
  readonly collision_run_summary?: RunSummary | null;
  readonly project_id?: string | null;
  readonly repo_provision: RepoProvisionStatus;
  readonly engine_key?: string | null;
}

export interface ChatSessionSnapshot {
  readonly runSummary: RunSummary;
  readonly collisionRunSummary: RunSummary | null;
  readonly projectId: string | null;
  readonly repoProvision: RepoProvisionStatus;
  readonly engineKey: string | null | undefined;
}

export interface ChatSessionQueryView {
  readonly data: ChatSessionSnapshot | undefined;
  readonly isError: boolean;
}

function toChatSessionSnapshot(session: ChatSessionWire): ChatSessionSnapshot {
  return {
    runSummary: session.run_summary,
    collisionRunSummary: session.collision_run_summary ?? null,
    projectId: session.project_id ?? null,
    repoProvision: session.repo_provision,
    engineKey: session.engine_key ?? undefined,
  };
}

export function useChatSessionQuery<W extends ChatSessionWire>(
  family: string,
  sessionId: string | null,
  get: (id: string) => Promise<W>,
): ChatSessionQueryView {
  const query = useQuery<W, unknown, ChatSessionSnapshot>(
    {
      queryKey: chatSessionKeyOf(family, sessionId),
      queryFn: () => get(sessionId!),
      enabled: sessionId !== null,
      select: toChatSessionSnapshot,
      staleTime: 0,
      refetchOnMount: "always",
      gcTime: 0,
      placeholderData: NO_PLACEHOLDER,
      refetchInterval: (q) => (q.state.data?.repo_provision === "pending" ? 1500 : false),
      refetchIntervalInBackground: true,
    },
    queryClient,
  );
  return { data: query.data, isError: query.isError };
}

export function fetchChatSession<T extends ChatSessionWire>(
  family: string,
  sessionId: string,
  get: (id: string) => Promise<T>,
): Promise<T> {
  return queryClient.fetchQuery<T>({
    queryKey: chatSessionKeyOf(family, sessionId),
    queryFn: () => get(sessionId),
    staleTime: 0,
  });
}

export function useInvalidateChatSession(
  family: string,
  sessionId: string | null,
): () => Promise<void> {
  const client = useQueryClient(queryClient);
  const queryKey = useMemo(() => chatSessionKeyOf(family, sessionId), [family, sessionId]);
  return useCallback(async () => {
    if (sessionId === null) return;
    await client.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: true });
  }, [client, queryKey, sessionId]);
}

export interface WorkspaceDetailView {
  readonly data: WorkspaceDetail | undefined;
  readonly isError: boolean;
  readonly error: unknown;
  readonly epoch: number;
}

export function useWorkspaceDetailQuery(taskId: string | undefined): WorkspaceDetailView {
  const client = useQueryClient(queryClient);
  const queryKey = workspaceKeyOf(taskId);
  const query = useQuery(
    {
      queryKey,
      queryFn: () => api.getWorkspace(taskId!, { includeTriage: true }),
      enabled: taskId !== undefined,
      placeholderData: NO_PLACEHOLDER,
      structuralSharing: false,
      notifyOnChangeProps: "all",
      gcTime: 0,
      staleTime: 0,
      refetchOnMount: "always",
    },
    queryClient,
  );
  const epoch = client.getQueryState(queryKey)!.dataUpdateCount;
  return { data: query.data, isError: query.isError, error: query.error, epoch };
}

export function useWorkspaceRepoKey(taskId: string | undefined): string | null | undefined {
  const query = useQuery(
    {
      queryKey: workspaceRepoKeyOf(taskId),
      queryFn: () => api.getWorkspace(taskId!),
      select: (detail) => detail.engine_key ?? null,
      enabled: taskId !== undefined,
      placeholderData: NO_PLACEHOLDER,
      staleTime: Infinity,
    },
    queryClient,
  );
  return query.data;
}

export function useWorkspaceInvocationsQuery(
  taskId: string | undefined,
  epoch: number,
  enabled: boolean,
): DocQueryView<string[]> {
  const query = useQuery(
    {
      queryKey: [...workspaceInvocationsPrefixOf(taskId), epoch] as const,
      queryFn: () => api.listInvocations(taskId!),
      enabled: enabled && taskId !== undefined,
      placeholderData: NO_PLACEHOLDER,
      gcTime: 0,
      staleTime: 0,
      refetchOnMount: "always",
    },
    queryClient,
  );
  return { data: query.data, isError: query.isError, error: query.error };
}

export function useRefreshWorkspaceDetail(taskId: string | undefined): () => void {
  const client = useQueryClient(queryClient);
  return useCallback(() => {
    if (taskId === undefined) return;
    const workspaceKey = workspaceKeyOf(taskId);
    void client.cancelQueries({ queryKey: workspaceInvocationsPrefixOf(taskId) });
    void client.cancelQueries({ queryKey: workspaceKey, exact: true });
    void client.refetchQueries({ queryKey: workspaceKey, exact: true });
  }, [client, taskId]);
}

export interface ActionCallbacks<T> {
  readonly onSuccess?: (value: T) => void;
  readonly onError?: (error: unknown) => void;
}

export interface ActionMutationView<T> {
  readonly run: (callbacks?: ActionCallbacks<T>) => void;
  readonly isPending: boolean;
  readonly isSuccess: boolean;
  readonly isError: boolean;
  readonly error: unknown;
}

export function useActionMutation<T>(action: () => Promise<T>): ActionMutationView<T> {
  const mutation = useMutation<T, unknown, void>(
    { mutationFn: action, networkMode: "always" },
    queryClient,
  );
  const { mutate } = mutation;
  const run = useCallback(
    (callbacks?: ActionCallbacks<T>) => {
      mutate(undefined, {
        onSuccess: (value) => callbacks?.onSuccess?.(value),
        onError: (error) => callbacks?.onError?.(error),
      });
    },
    [mutate],
  );
  return {
    run,
    isPending: mutation.isPending,
    isSuccess: mutation.isSuccess,
    isError: mutation.isError,
    error: mutation.error,
  };
}
