const STORAGE_KEY = "taskDrawerWidth";
export const DRAWER_MIN_WIDTH = 360;
export const DRAWER_DEFAULT_WIDTH = 560;

export function clampDrawerWidth(width: number, viewportWidth: number): number {
  const maxWidth = Math.max(DRAWER_MIN_WIDTH, Math.floor(viewportWidth * 0.9));
  if (!Number.isFinite(width)) return DRAWER_DEFAULT_WIDTH;
  if (width < DRAWER_MIN_WIDTH) return DRAWER_MIN_WIDTH;
  if (width > maxWidth) return maxWidth;
  return Math.floor(width);
}

export function loadDrawerWidth(storage: Storage | null, viewportWidth: number): number {
  if (!storage) return DRAWER_DEFAULT_WIDTH;
  const raw = storage.getItem(STORAGE_KEY);
  if (!raw) return DRAWER_DEFAULT_WIDTH;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return DRAWER_DEFAULT_WIDTH;
  return clampDrawerWidth(parsed, viewportWidth);
}

export function saveDrawerWidth(storage: Storage | null, width: number): void {
  if (!storage) return;
  storage.setItem(STORAGE_KEY, String(Math.floor(width)));
}
