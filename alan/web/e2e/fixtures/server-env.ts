import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(here, "..", "..", "..", "..");
export const stubDir = here;

const portNum = Number(process.env.E2E_PORT);
export const E2E_PORT_BASE =
  Number.isInteger(portNum) && portNum > 0 ? portNum : 4300;

const rootEnv = process.env.ALAN_E2E_ROOT;
export const E2E_ROOT_BASE =
  rootEnv !== undefined && rootEnv !== ""
    ? rootEnv
    : path.join(os.homedir(), ".alan-playwright");

const workersNum = Number(process.env.E2E_WORKERS);
export const E2E_WORKERS =
  Number.isInteger(workersNum) && workersNum > 0 ? workersNum : 4;

export function workerPort(parallelIndex: number): number {
  return E2E_PORT_BASE + parallelIndex;
}

export function workerRoot(parallelIndex: number): string {
  return path.join(E2E_ROOT_BASE, `w${parallelIndex}`);
}

export function ghBranchSuffix(parallelIndex: number): string {
  return `w${parallelIndex}-p${process.pid}`;
}

export function enginesRoot(root: string): string {
  return path.join(root, "engines");
}

export function ghRemotesRoot(root: string): string {
  return path.join(root, "gh-remotes");
}

function ensureGitConfigGlobal(root: string): string {
  const remotesRoot = ghRemotesRoot(root);
  fs.mkdirSync(remotesRoot, { recursive: true });
  const configPath = path.join(root, "e2e.gitconfig");
  fs.writeFileSync(
    configPath,
    `[user]\n\tname = alan-playwright\n\temail = alan-playwright@localhost\n[url "file://${remotesRoot}/"]\n\tinsteadOf = https://github.com/\n`,
  );
  return configPath;
}

function ensureWorktreeRepo(root: string, gitConfigGlobal: string): string {
  const repoDir = path.join(root, "worktree-repo");
  fs.mkdirSync(repoDir, { recursive: true });
  const gitOpts = { cwd: repoDir, env: { ...process.env, GIT_CONFIG_GLOBAL: gitConfigGlobal } };
  execFileSync("git", ["init", "-q", "-b", "main"], gitOpts);
  execFileSync(
    "git",
    ["commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "init"],
    gitOpts,
  );
  return repoDir;
}

export function serverEnv(
  root: string,
  ghBranch: string,
): Record<string, string> {
  const gitConfigGlobal = ensureGitConfigGlobal(root);
  return {
    GIT_CONFIG_GLOBAL: gitConfigGlobal,
    ALAN_WORKTREE_REPO_ROOT: ensureWorktreeRepo(root, gitConfigGlobal),
    ALAN_WORKSPACES_ROOT: path.join(root, "workspaces"),
    ALAN_QUEUE_DB_PATH: path.join(root, "queue.db"),
    ALAN_BUILD_CHAT_SESSIONS_ROOT: path.join(root, "build-chat-sessions"),
    ALAN_EXPLORE_CHAT_SESSIONS_ROOT: path.join(root, "explore-chat-sessions"),
    ALAN_PROJECTS_ROOT: path.join(root, "projects"),
    ALAN_SECRETS_STORE_ROOT: path.join(root, "secrets"),
    ALAN_ENGINES_ROOT: enginesRoot(root),
    ALAN_PLANS_ROOT: path.join(root, "plans"),
    ALAN_STATE_ROOT: path.join(root, "state"),
    ALAN_DRIVER_LOOP_STATE_ROOT: path.join(root, "driver-loop-state"),
    ALAN_SLOT_POOL_ROOT: "",
    ALAN_DETACHED_WORKERS: "off",
    ALAN_UNBLOCK_DISABLE: "1",
    ALAN_WEB_DIST_DIR: "",
    ALAN_IMPL_DRY_RUN: "1",
    ALAN_VERIFICATION_DRY_RUN: "1",
    ALAN_PR_MERGE_DRY_RUN: "1",
    ALAN_DEPLOY_VERIFY_DRY_RUN: "1",
    ALAN_DEP_SWEEP_DRY_RUN: "1",
    ALAN_E2E_HEALTH_FIXTURE: "",
    ALAN_E2E_DESKTOP_MAX_HEALTH_FIXTURE: "",
    ALAN_E2E_HEALTH_REPOS_FIXTURE: "",
    ALAN_PR_MERGE_POLL_INTERVAL_S: "0",
    ALAN_PR_MERGE_TIMEOUT_S: "5",
    ALAN_PR_MERGE_MAX_POLLS: "20",
    ALAN_E2E_PR_MERGE_FLOW: "1",
    ALAN_E2E_GH_MERGE_MARKER: path.join(root, "gh-merge-marker.log"),
    ALAN_E2E_GH_PR_STATE_FILE: path.join(root, "gh-pr-state.json"),
    ALAN_E2E_GH_CHECKS_STATE_FILE: path.join(root, "gh-checks-state"),
    ALAN_E2E_GH_COPILOT_THREADS_FILE: path.join(root, "gh-copilot-threads"),
    ALAN_E2E_GH_COPILOT_COMMENTS_FILE: path.join(root, "gh-copilot-comments"),
    ALAN_E2E_GH_OAUTH_SCOPES_FILE: path.join(root, "gh-oauth-scopes"),
    ALAN_E2E_GH_BRANCH: ghBranch,
    ALAN_LOGS_ROOT: path.join(root, "logs"),
    ALAN_E2E_STUB_MARKER: path.join(root, "stub-marker.log"),
    ALAN_E2E_STUB_HANG_FILE: path.join(root, "stub-hang"),
    ALAN_DISPATCHER_CONCURRENCY: "4",
    ALAN_STUB_DELAY_MS: "200",
    ALAN_E2E_USAGE_LIMIT_RESET_IN_MINUTES: "1",
    ALAN_E2E_USAGE_LIMIT_TRIGGER_FILE: path.join(root, "usage-limit-trigger"),
    ALAN_USAGE_LIMIT_SAFETY_BUFFER_SECONDS: "1",
    ALAN_SERVER_ERROR_BASE_DELAY_S: "1",
    ALAN_SERVER_ERROR_DELAY_CAP_S: "5",
    ALAN_E2E_SERVER_ERROR_TRIGGER_FILE: path.join(root, "server-error-trigger"),
    ALAN_E2E_VERIFICATION_CANNOT_VERIFY: path.join(root, "cannot-verify-trigger"),
    ALAN_STUB_RENDEZVOUS_FILE: path.join(root, "rendezvous.log"),
    ALAN_STUB_RENDEZVOUS_N: "2",
    ALAN_STUB_RENDEZVOUS_TIMEOUT_MS: "15000",
    ALAN_E2E_GH_DELIVERY_FILE: path.join(root, "gh-delivery.json"),
    ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S: "0.2",
    ALAN_VERIFIER_SETTLE_MAX_POLLS: "3",
  };
}
