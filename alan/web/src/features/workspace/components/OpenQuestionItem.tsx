import type { JSX } from "react";

import type { OpenQuestion } from "../../../api";

function OpenQuestionOptions({ options }: Readonly<{ options: string[] }>): JSX.Element | null {
  if (options.length === 0) return null;
  return (
    <div className="text-[10px] text-text-muted">
      <span>Options:</span>
      <ul data-testid="open-question-options">
        {options.map((option) => (
          <li key={option} data-testid="open-question-option">
            {option}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function OpenQuestionItem({ question }: Readonly<{ question: OpenQuestion }>): JSX.Element {
  const priorAttempts = question.prior_attempts!;
  return (
    <li className="space-y-0.5 border-b border-border/60 py-1.5 last:border-0">
      <div className="flex items-start gap-2">
        <span className="text-xs">{question.question}</span>
        {question.blocking && (
          <span className="rounded bg-red-900/30 px-1 py-0.5 text-[10px] text-red-400">blocking</span>
        )}
      </div>
      <div className="text-[10px] text-text-muted">resolver: {question.resolver_role}</div>
      {priorAttempts.length > 0 && (
        <div className="text-[10px] text-text-muted">Prior attempts: {priorAttempts.join(", ")}</div>
      )}
      <OpenQuestionOptions options={question.proposed_answer_space!} />
      {question.answer != null && (
        <output data-testid="open-question-answer" className="block text-[10px] text-text-muted">
          Answer: {question.answer}
        </output>
      )}
    </li>
  );
}

export function OpenQuestionsSection({
  questions,
}: Readonly<{ questions: OpenQuestion[] }>): JSX.Element | null {
  if (questions.length === 0) return null;
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-medium text-text-primary">Open Questions</h3>
      <ul>
        {questions.map((q) => (
          <OpenQuestionItem key={q.id} question={q} />
        ))}
      </ul>
    </div>
  );
}
