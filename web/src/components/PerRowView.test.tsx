import { describe, expect, it } from "vitest";
import {
  countRelations,
  extractBatchRows,
  extractClassifications,
  extractEntities,
  extractOutputsByIndex,
  pairRowsAndOutputs,
  summarizeJsonStructures,
} from "./PerRowView";
import type { TaskDetailArtifact } from "../types";

function artifact(
  kind: string,
  payload: unknown,
  id = "art-1",
): TaskDetailArtifact {
  return {
    artifact_id: id,
    task_id: "t-1",
    kind,
    path: "",
    content_type: "application/json",
    created_at: "2026-01-01T00:00:00Z",
    metadata: {},
    payload,
  };
}

describe("extractBatchRows", () => {
  it("returns [] for non-batched source_ref", () => {
    expect(extractBatchRows({})).toEqual([]);
    expect(extractBatchRows(null)).toEqual([]);
    expect(extractBatchRows({ payload: {} })).toEqual([]);
    expect(extractBatchRows({ payload: { rows: "nope" } })).toEqual([]);
  });

  it("extracts rows with row_index and input", () => {
    const ref = {
      payload: {
        rows: [
          { row_index: 0, row_id: "r0", input: "abc" },
          { row_index: 1, row_id: "r1", input: "def" },
        ],
      },
    };
    expect(extractBatchRows(ref)).toEqual([
      { row_index: 0, row_id: "r0", source_id: undefined, input: "abc" },
      { row_index: 1, row_id: "r1", source_id: undefined, input: "def" },
    ]);
  });

  it("falls back to array index when row_index missing", () => {
    const ref = { payload: { rows: [{ input: "a" }, { input: "b" }] } };
    expect(extractBatchRows(ref).map((r) => r.row_index)).toEqual([0, 1]);
  });
});

describe("extractOutputsByIndex", () => {
  it("returns empty map when no annotation_result artifact", () => {
    expect(extractOutputsByIndex([])).toEqual(new Map());
    expect(extractOutputsByIndex([artifact("other", {})])).toEqual(new Map());
  });

  it("reads rows from latest annotation_result", () => {
    const arts = [
      artifact("annotation_result", { rows: [{ row_index: 0, output: { relations: [] } }] }, "old"),
      artifact(
        "annotation_result",
        {
          rows: [
            { row_index: 0, output: { entities: { person: ["A"] } } },
            { row_index: 1, output: { relations: [{ a: 1 }] } },
          ],
        },
        "new",
      ),
    ];
    const m = extractOutputsByIndex(arts);
    expect(m.size).toBe(2);
    expect(m.get(0)).toEqual({ entities: { person: ["A"] } });
    expect(m.get(1)).toEqual({ relations: [{ a: 1 }] });
  });

  it("unwraps stringified payload envelopes", () => {
    const inner = JSON.stringify({
      rows: [{ row_index: 3, output: { entities: { org: ["ACME"] } } }],
    });
    const arts = [artifact("annotation_result", { text: inner })];
    const m = extractOutputsByIndex(arts);
    expect(m.get(3)).toEqual({ entities: { org: ["ACME"] } });
  });
});

describe("pairRowsAndOutputs", () => {
  it("pairs by row_index, leaving output undefined when missing", () => {
    const rows = [
      { row_index: 0, input: "a" },
      { row_index: 1, input: "b" },
    ];
    const outputs = new Map<number, { entities?: Record<string, unknown> }>([
      [0, { entities: { person: ["X"] } }],
    ]);
    const paired = pairRowsAndOutputs(rows, outputs);
    expect(paired[0].output).toEqual({ entities: { person: ["X"] } });
    expect(paired[1].output).toBeUndefined();
  });
});

describe("summarizeJsonStructures", () => {
  it("flags empty for null / undefined / {} / []", () => {
    expect(summarizeJsonStructures(null).empty).toBe(true);
    expect(summarizeJsonStructures(undefined).empty).toBe(true);
    expect(summarizeJsonStructures({}).empty).toBe(true);
    expect(summarizeJsonStructures([]).empty).toBe(true);
  });

  it("returns legacy count for array form", () => {
    const s = summarizeJsonStructures([{ a: 1 }, { b: 2 }, { c: 3 }]);
    expect(s.legacyCount).toBe(3);
    expect(s.empty).toBe(false);
    expect(s.newSchemaTypes).toEqual([]);
  });

  it("returns per-type counts for new-schema object form", () => {
    const s = summarizeJsonStructures({
      status: [{ phrase: "p1" }],
      goal: [{ phrase: "g1" }, { phrase: "g2" }],
      empty_type: [],
    });
    expect(s.legacyCount).toBeNull();
    expect(s.empty).toBe(false);
    const map = new Map(s.newSchemaTypes);
    expect(map.get("status")).toBe(1);
    expect(map.get("goal")).toBe(2);
    expect(map.has("empty_type")).toBe(false);
  });
});

describe("countRelations", () => {
  it("counts array length", () => {
    expect(countRelations([1, 2, 3])).toBe(3);
    expect(countRelations([])).toBe(0);
  });
  it("returns 0 for non-arrays", () => {
    expect(countRelations(null)).toBe(0);
    expect(countRelations({})).toBe(0);
    expect(countRelations(undefined)).toBe(0);
  });
});

describe("extractClassifications", () => {
  it("returns task/final_label pairs", () => {
    const c = extractClassifications([
      { task: "intent", final_label: "information_request" },
      { task: "sentiment", final_label: "neutral" },
    ]);
    expect(c).toEqual([
      { task: "intent", final_label: "information_request" },
      { task: "sentiment", final_label: "neutral" },
    ]);
  });
  it("falls back to label when final_label absent", () => {
    const c = extractClassifications([{ task: "intent", label: "info" }]);
    expect(c).toEqual([{ task: "intent", final_label: "info" }]);
  });
  it("ignores malformed entries", () => {
    expect(extractClassifications([null, {}, { task: "x" }])).toEqual([]);
    expect(extractClassifications("nope" as unknown)).toEqual([]);
  });
});

describe("extractEntities", () => {
  it("groups dict form values", () => {
    const g = extractEntities({ person: ["Alice", "Bob"], org: ["ACME"] });
    expect(g).toEqual([
      { type: "person", values: ["Alice", "Bob"] },
      { type: "org", values: ["ACME"] },
    ]);
  });
  it("handles legacy array-of-records form", () => {
    const g = extractEntities([
      { type: "person", text: "Alice" },
      { type: "person", text: "Bob" },
      { type: "org", text: "ACME" },
    ]);
    const byType = new Map(g.map((e) => [e.type, e.values]));
    expect(byType.get("person")).toEqual(["Alice", "Bob"]);
    expect(byType.get("org")).toEqual(["ACME"]);
  });
  it("returns [] for null/undefined", () => {
    expect(extractEntities(null)).toEqual([]);
    expect(extractEntities(undefined)).toEqual([]);
    expect(extractEntities({})).toEqual([]);
  });
});
