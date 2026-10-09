import { useFeedback } from "../notifications";

type WorkspaceFeedback = {
  error: string;
  notice: string;
  loading?: string;
  onDismissError: () => void;
  onDismissNotice: () => void;
};

export function useWorkspaceNotifications({ error, notice, loading, onDismissError, onDismissNotice }: WorkspaceFeedback) {
  useFeedback({ error, notice, loading, clearError: onDismissError, clearNotice: onDismissNotice });
}

export function WorkspaceNotifications(props: WorkspaceFeedback) {
  useWorkspaceNotifications(props);
  return null;
}
