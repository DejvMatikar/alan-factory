import ErrorBanner from "../../../ui/ErrorBanner";
import Button from "../../../components/Button";

export interface QuestionsTabProps {
  questions: string[];
  answers: Record<number, string>;
  onAnswer: (i: number, v: string) => void;
  onDispatch: () => void;
  dispatching: boolean;
  error: string | null;
  actionLabel: string;
  busyLabel: string;
  testId: string;
}

export default function QuestionsTab({
  questions,
  answers,
  onAnswer,
  onDispatch,
  dispatching,
  error,
  actionLabel,
  busyLabel,
  testId,
}: Readonly<QuestionsTabProps>) {
  return (
    <div className="flex flex-col gap-3 flex-1">
      <p className="text-sm text-text-muted">Answer these open questions, then dispatch.</p>
      {questions.map((q, i) => (
        <div key={`${String(i)}-${q.slice(0, 30)}`} className="flex flex-col gap-1">
          <label htmlFor={`answer-${i}`} className="text-sm text-text-primary">
            {q}
          </label>
          <textarea
            id={`answer-${i}`}
            data-testid={`answer-${i}`}
            value={answers[i] ?? ""}
            onChange={(e) => onAnswer(i, e.target.value)}
            rows={2}
            className="w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary"
          />
        </div>
      ))}
      {error !== null && <ErrorBanner>{error}</ErrorBanner>}
      <Button
        variant="action"
        onClick={onDispatch}
        disabled={
          dispatching || questions.some((_q, i) => (answers[i] ?? "").trim().length === 0)
        }
        data-testid={testId}
      >
        {dispatching ? busyLabel : actionLabel}
      </Button>
    </div>
  );
}
