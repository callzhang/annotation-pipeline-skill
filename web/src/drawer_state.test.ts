import { describe, expect, it } from "vitest";
import {
  DRAWER_DEFAULT_WIDTH,
  DRAWER_MIN_WIDTH,
  clampDrawerWidth,
  loadDrawerWidth,
  saveDrawerWidth,
} from "./drawer_state";

function makeStorage(initial: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(initial));
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => (store.has(key) ? (store.get(key) as string) : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
  };
}

describe("drawer_state", () => {
  it("clamps below the min width", () => {
    expect(clampDrawerWidth(100, 1600)).toBe(DRAWER_MIN_WIDTH);
  });

  it("clamps above 90% of viewport", () => {
    expect(clampDrawerWidth(2000, 1000)).toBe(900);
  });

  it("returns finite integer width within range", () => {
    expect(clampDrawerWidth(640.7, 1600)).toBe(640);
  });

  it("falls back to default when finite check fails", () => {
    expect(clampDrawerWidth(Number.NaN, 1600)).toBe(DRAWER_DEFAULT_WIDTH);
  });

  it("loads default when storage has no value", () => {
    expect(loadDrawerWidth(makeStorage(), 1600)).toBe(DRAWER_DEFAULT_WIDTH);
  });

  it("loads default when storage entry is unparseable", () => {
    expect(loadDrawerWidth(makeStorage({ taskDrawerWidth: "huge" }), 1600)).toBe(DRAWER_DEFAULT_WIDTH);
  });

  it("loads and clamps a stored value", () => {
    expect(loadDrawerWidth(makeStorage({ taskDrawerWidth: "200" }), 1600)).toBe(DRAWER_MIN_WIDTH);
    expect(loadDrawerWidth(makeStorage({ taskDrawerWidth: "700" }), 1600)).toBe(700);
  });

  it("saves the width to storage", () => {
    const storage = makeStorage();
    saveDrawerWidth(storage, 612.5);
    expect(storage.getItem("taskDrawerWidth")).toBe("612");
  });

  it("is a no-op when storage is null", () => {
    expect(() => saveDrawerWidth(null, 600)).not.toThrow();
    expect(loadDrawerWidth(null, 1600)).toBe(DRAWER_DEFAULT_WIDTH);
  });
});
