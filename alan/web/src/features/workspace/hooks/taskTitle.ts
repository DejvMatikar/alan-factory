import type { Task } from "../../../api";
import { formatTeamLabel } from "./teamLabel";

export function deriveTaskTitle(task: Task, wsTitle = ""): string {
  if (task.title != null && task.title.length > 0) return task.title;
  if (task.parent_task_id == null && wsTitle.length > 0) return wsTitle;
  if (task.parent_task_id != null) return task.id;
  return formatTeamLabel(task.team ?? task.id);
}
