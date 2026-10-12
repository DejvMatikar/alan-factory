import { keepPreviousData, notifyManager, QueryClient } from "@tanstack/react-query";

notifyManager.setScheduler(queueMicrotask);

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      networkMode: "always",
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      staleTime: Infinity,
      placeholderData: keepPreviousData,
      throwOnError: false,
    },
  },
});
