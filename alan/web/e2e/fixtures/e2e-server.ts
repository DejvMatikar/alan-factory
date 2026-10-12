import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { installTestDefaultProfile } from "./default-profile";
import { ghBranchSuffix, repoRoot, serverEnv, stubDir, workerPort, workerRoot } from "./server-env";

export interface E2eServer {
  port: number;
  root: string;
  baseURL: string;
  logPath: string;
  stop: () => Promise<void>;
}

export function listeningPids(port: number): string[] {
  try {
    return execFileSync("lsof", ["-t", `-iTCP:${port}`, "-sTCP:LISTEN"], {
      encoding: "utf8",
    })
      .trim()
      .split("\n")
      .filter(Boolean);
  } catch {
    return [];
  }
}

export function freePortOrThrow(port: number): void {
  for (const pid of listeningPids(port)) {
    let cmd = "";
    try {
      cmd = execFileSync("ps", ["-p", pid, "-o", "command="], {
        encoding: "utf8",
      }).trim();
    } catch {
      continue;
    }
    if (/python.* -m alan/.test(cmd)) {
      try {
        process.kill(Number(pid), "SIGKILL");
      } catch {
      }
    } else {
      throw new Error(
        `non-alan process holds 127.0.0.1:${port}: PID ${pid} ${cmd}`,
      );
    }
  }
}

export async function startServer(parallelIndex: number): Promise<E2eServer> {
  const port = workerPort(parallelIndex);
  const root = workerRoot(parallelIndex);
  freePortOrThrow(port);
  fs.rmSync(root, { recursive: true, force: true });
  fs.mkdirSync(path.join(root, "workspaces"), { recursive: true });
  installTestDefaultProfile(root);
  const logPath = path.join(root, "server.log");
  const log = fs.openSync(logPath, "a");
  const child = spawn(
    "uv",
    ["run", "python", "-m", "alan", "--port", String(port), "--host", "127.0.0.1"],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        PATH: `${stubDir}:${process.env.PATH ?? ""}`,
        ...serverEnv(root, `e2e-stub-pr-branch-${ghBranchSuffix(parallelIndex)}`),
      },
      stdio: ["ignore", log, log],
      detached: true,
    },
  );
  let exited = false;
  child.on("exit", () => {
    exited = true;
  });

  const killGroup = (signal: NodeJS.Signals): void => {
    if (child.pid === undefined) return;
    try {
      process.kill(-child.pid, signal);
    } catch {
    }
  };

  const deadline = Date.now() + 60_000;
  for (;;) {
    if (exited) {
      fs.closeSync(log);
      throw new Error(
        `alan server (worker ${parallelIndex}) exited during boot; see ${logPath}`,
      );
    }
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (res.ok) break;
    } catch {
    }
    if (Date.now() > deadline) {
      killGroup("SIGKILL");
      fs.closeSync(log);
      throw new Error(
        `alan server (worker ${parallelIndex}) failed /api/health within 60s; see ${logPath}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }

  const stop = async (): Promise<void> => {
    killGroup("SIGTERM");
    const graceful = Date.now() + 5_000;
    while (!exited && Date.now() < graceful) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!exited) killGroup("SIGKILL");
    fs.closeSync(log);
  };

  return { port, root, baseURL: `http://127.0.0.1:${port}`, logPath, stop };
}
