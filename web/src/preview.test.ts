import { describe, expect, it } from "vitest";
import { previewArtifacts, previewBoxes, previewImageSource, previewTitle } from "./preview";
import type { TaskDetailArtifact } from "./types";

const artifact: TaskDetailArtifact = {
  artifact_id: "artifact-1",
  task_id: "task-1",
  kind: "image_bbox_preview",
  path: "artifact_payloads/task-1/preview.json",
  content_type: "application/json",
  created_at: "2026-05-05T00:00:00+00:00",
  metadata: { provider: "vc_detector" },
  payload: {
    image_url: "http://127.0.0.1/image.png",
    boxes: [{ label: "person", score: 0.91, x: 0.1, y: 0.2, width: 0.3, height: 0.4 }],
  },
};

describe("preview helpers", () => {
  it("selects preview artifacts", () => {
    expect(previewArtifacts([artifact, { ...artifact, artifact_id: "annotation", kind: "annotation_result" }])).toEqual([artifact]);
  });

  it("extracts image source and normalized boxes", () => {
    expect(previewImageSource(artifact)).toBe("http://127.0.0.1/image.png");
    expect(previewBoxes(artifact)).toEqual([
      { label: "person", score: 0.91, left: 10, top: 20, width: 30, height: 40 },
    ]);
  });

  it("formats preview titles", () => {
    expect(previewTitle(artifact)).toBe("image_bbox_preview · vc_detector");
  });
});
