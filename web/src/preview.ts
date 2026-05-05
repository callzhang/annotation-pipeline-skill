import type { TaskDetailArtifact } from "./types";

export interface PreviewBox {
  label: string;
  score: number | null;
  left: number;
  top: number;
  width: number;
  height: number;
}

export function previewArtifacts(artifacts: TaskDetailArtifact[]): TaskDetailArtifact[] {
  return artifacts.filter((artifact) => artifact.kind.endsWith("_preview") || artifact.kind.includes("bbox"));
}

export function previewImageSource(artifact: TaskDetailArtifact): string | null {
  const payload = objectPayload(artifact.payload);
  const metadata = artifact.metadata;
  return stringValue(payload.image_url)
    ?? stringValue(payload.image)
    ?? stringValue(payload.rendered_image_url)
    ?? stringValue(metadata.image_url)
    ?? stringValue(metadata.rendered_image_url);
}

export function previewBoxes(artifact: TaskDetailArtifact): PreviewBox[] {
  const payload = objectPayload(artifact.payload);
  const rawBoxes = Array.isArray(payload.boxes) ? payload.boxes : Array.isArray(payload.bounding_boxes) ? payload.bounding_boxes : [];
  return rawBoxes.flatMap((item) => {
    const box = objectPayload(item);
    const normalized = normalizeBox(box);
    if (!normalized) return [];
    return [{
      label: stringValue(box.label) ?? stringValue(box.class) ?? "object",
      score: numberValue(box.score) ?? numberValue(box.confidence),
      ...normalized,
    }];
  });
}

export function previewTitle(artifact: TaskDetailArtifact): string {
  const provider = stringValue(artifact.metadata.provider) ?? stringValue(artifact.metadata.model) ?? artifact.content_type;
  return `${artifact.kind} · ${provider}`;
}

function normalizeBox(box: Record<string, unknown>): Pick<PreviewBox, "left" | "top" | "width" | "height"> | null {
  const x = numberValue(box.x);
  const y = numberValue(box.y);
  const width = numberValue(box.width) ?? numberValue(box.w);
  const height = numberValue(box.height) ?? numberValue(box.h);
  if (x !== null && y !== null && width !== null && height !== null) {
    return toPercentBox(x, y, width, height);
  }

  const xmin = numberValue(box.xmin) ?? numberValue(box.left);
  const ymin = numberValue(box.ymin) ?? numberValue(box.top);
  const xmax = numberValue(box.xmax) ?? numberValue(box.right);
  const ymax = numberValue(box.ymax) ?? numberValue(box.bottom);
  if (xmin === null || ymin === null || xmax === null || ymax === null) return null;
  return toPercentBox(xmin, ymin, xmax - xmin, ymax - ymin);
}

function toPercentBox(x: number, y: number, width: number, height: number): Pick<PreviewBox, "left" | "top" | "width" | "height"> {
  const scale = Math.max(Math.abs(x), Math.abs(y), Math.abs(width), Math.abs(height)) <= 1 ? 100 : 1;
  return {
    left: clamp(x * scale),
    top: clamp(y * scale),
    width: clamp(width * scale),
    height: clamp(height * scale),
  };
}

function clamp(value: number): number {
  return Math.max(0, Math.min(100, value));
}

function objectPayload(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
