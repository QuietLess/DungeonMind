import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ApiError } from "./api";
import type { Campaign, User } from "./types";

/** Load data on mount and when `deps` change; `reload` refetches. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    load()
      .then((value) => {
        if (live) {
          setData(value);
          setError(null);
        }
      })
      .catch((e: unknown) => live && setError(message(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, setData, error, loading, reload };
}

export function message(e: unknown): string {
  if (e instanceof ApiError || e instanceof Error) return e.message;
  return "Something went wrong.";
}

export interface Session {
  user: User | null;
  setUser: (user: User | null) => void;
}

export const SessionContext = createContext<Session>({ user: null, setUser: () => {} });
export const useSession = () => useContext(SessionContext);

export interface CampaignContextValue {
  campaign: Campaign;
  user: User;
}

export const CampaignContext = createContext<CampaignContextValue | null>(null);

export function useCampaign(): CampaignContextValue {
  const value = useContext(CampaignContext);
  if (!value) throw new Error("useCampaign outside a campaign");
  return value;
}
