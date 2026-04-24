import { describe, expect, it } from "vitest";
import { cardSubtitle, countCards, visibleColumns } from "./kanban";
import type { KanbanSnapshot } from "./types";

const snapshot: KanbanSnapshot = {
  columns: [
    {
      id: "ready",
      title: "Ready",
      cards: [
        {
          task_id: "task-1",
          status: "ready",
          modality: "text",
          annotation_types: ["entity_span"],
          selected_annotator_id: null,
          status_age_seconds: 3,
          latest_attempt_status: null,
          feedback_count: 0,
          retry_pending: false,
          blocked: false,
          external_sync_pending: false,
        },
      ],
    },
    { id: "human_review", title: "Human Review", cards: [] },
  ],
};

describe("kanban helpers", () => {
  it("counts cards across columns", () => {
    expect(countCards(snapshot)).toBe(1);
  });

  it("keeps empty operational columns visible", () => {
    expect(visibleColumns(snapshot).map((column) => column.id)).toEqual(["ready", "human_review"]);
  });

  it("builds a compact card subtitle from modality and annotation types", () => {
    expect(cardSubtitle({ modality: "image", annotation_types: ["bounding_box", "segmentation"] })).toBe(
      "image · bounding_box, segmentation",
    );
  });
});
