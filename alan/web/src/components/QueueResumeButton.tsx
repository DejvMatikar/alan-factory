import { useState } from "react";

import { api } from "../api";
import Button from "./Button";

export interface QueueResumeButtonProps {
  onResumed: () => void;
  onFailed: () => void;
}

export default function QueueResumeButton({ onResumed, onFailed }: Readonly<QueueResumeButtonProps>) {
  const [pending, setPending] = useState(false);

  const handleClick = () => {
    setPending(true);
    api.resumeQueue().then(
      () => {
        setPending(false);
        onResumed();
      },
      () => {
        setPending(false);
        onFailed();
      },
    );
  };

  return (
    <Button
      variant="action"
      data-testid="queue-resume-button"
      disabled={pending}
      onClick={handleClick}
    >
      Resume queue
    </Button>
  );
}
