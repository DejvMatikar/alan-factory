import type { EnginePreflightFinding } from "../api";

const FINDING_STYLE: Record<EnginePreflightFinding["status"], string> = {
  fail: "bg-red-500/15 text-red-300",
  unknown: "bg-amber-500/15 text-amber-300",
  pass: "bg-green-500/15 text-green-300",
};

export interface FindingRowProps {
  finding: EnginePreflightFinding;
  testId: string;
}

export default function FindingRow({ finding, testId }: Readonly<FindingRowProps>) {
  const blocking = finding.status === "fail";
  return (
    <div
      role={blocking ? "alert" : undefined}
      data-testid={testId}
      className={`space-y-0.5 rounded-md px-3 py-2 text-xs ${FINDING_STYLE[finding.status]}`}
    >
      <p data-testid={`${testId}-message`}>{finding.message}</p>
      {finding.remedy !== "" && <p data-testid={`${testId}-remedy`}>{finding.remedy}</p>}
      {finding.remedy_url !== "" && (
        <a
          href={finding.remedy_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-btn-action underline"
          data-testid={`${testId}-link`}
          aria-label={finding.remedy !== "" ? finding.remedy : finding.message}
        >
          Open →
        </a>
      )}
    </div>
  );
}
