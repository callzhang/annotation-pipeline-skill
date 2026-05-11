import { useCallback, useEffect, useRef, useState } from "react";

export interface UrlState {
  view: string;
  store: string | null;
  project: string | null;
  task: string | null;
}

export interface UrlStateSetters {
  setView: (value: string) => void;
  setStore: (value: string | null) => void;
  setProject: (value: string | null) => void;
  setTask: (value: string | null) => void;
}

const STORE_LOCALSTORAGE_KEY = "storeKey";

/**
 * Pure: parse a `window.location.search`-style string into UrlState.
 * Missing or empty params fall back to defaults.
 */
export function parseUrlState(search: string, defaults: UrlState): UrlState {
  const params = new URLSearchParams(search);
  const view = params.get("view");
  const store = params.get("store");
  const project = params.get("project");
  const task = params.get("task");
  return {
    view: view && view.length > 0 ? view : defaults.view,
    store: store && store.length > 0 ? store : defaults.store,
    project: project && project.length > 0 ? project : defaults.project,
    task: task && task.length > 0 ? task : defaults.task,
  };
}

/**
 * Pure: serialize UrlState into a `?foo=bar&baz=qux` string.
 * Empty/null values are omitted entirely (cleaner URL).
 * If everything is empty, returns "".
 */
export function buildSearch(state: UrlState, defaults: UrlState): string {
  const params = new URLSearchParams();
  if (state.view && state.view !== defaults.view) {
    params.set("view", state.view);
  }
  if (state.store) {
    params.set("store", state.store);
  }
  if (state.project) {
    params.set("project", state.project);
  }
  if (state.task) {
    params.set("task", state.task);
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

/**
 * Write the given state to the browser URL via history.replaceState.
 * Replace (not push) keeps the back/forward stack uncluttered.
 */
export function writeUrl(state: UrlState, defaults: UrlState): void {
  if (typeof window === "undefined") return;
  const search = buildSearch(state, defaults);
  const url = `${window.location.pathname}${search}${window.location.hash}`;
  window.history.replaceState(window.history.state, "", url);
}

/**
 * React hook that mirrors `view`, `store`, `project`, `task` between
 * React state and the browser URL.
 *
 * - On mount: reads `window.location.search` and hydrates state.
 *   If `store` is absent from the URL, falls back to localStorage.
 * - State setters update both React state and the URL (replaceState).
 * - `setStore` also persists to localStorage.
 * - `popstate` events re-sync React state from the URL.
 */
export function useUrlState(defaults: UrlState): [UrlState, UrlStateSetters] {
  const defaultsRef = useRef(defaults);
  defaultsRef.current = defaults;

  const [state, setState] = useState<UrlState>(() => {
    if (typeof window === "undefined") return defaults;
    const parsed = parseUrlState(window.location.search, defaults);
    if (!parsed.store) {
      try {
        const stored = window.localStorage?.getItem(STORE_LOCALSTORAGE_KEY);
        if (stored) {
          return { ...parsed, store: stored };
        }
      } catch {
        // ignore localStorage errors
      }
    }
    return parsed;
  });

  // Keep the URL in sync if it diverges (e.g. SSR/initial mismatch).
  useEffect(() => {
    writeUrl(state, defaultsRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    function onPopState() {
      const parsed = parseUrlState(window.location.search, defaultsRef.current);
      setState(parsed);
    }
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
    };
  }, []);

  const updateField = useCallback(
    <K extends keyof UrlState>(key: K, value: UrlState[K]) => {
      setState((current) => {
        if (current[key] === value) return current;
        const next = { ...current, [key]: value };
        writeUrl(next, defaultsRef.current);
        return next;
      });
    },
    [],
  );

  const setView = useCallback(
    (value: string) => {
      updateField("view", value || defaultsRef.current.view);
    },
    [updateField],
  );

  const setStore = useCallback(
    (value: string | null) => {
      updateField("store", value);
      if (typeof window !== "undefined") {
        try {
          if (value) {
            window.localStorage?.setItem(STORE_LOCALSTORAGE_KEY, value);
          } else {
            window.localStorage?.removeItem(STORE_LOCALSTORAGE_KEY);
          }
        } catch {
          // ignore localStorage errors
        }
      }
    },
    [updateField],
  );

  const setProject = useCallback(
    (value: string | null) => {
      updateField("project", value);
    },
    [updateField],
  );

  const setTask = useCallback(
    (value: string | null) => {
      updateField("task", value);
    },
    [updateField],
  );

  return [state, { setView, setStore, setProject, setTask }];
}
