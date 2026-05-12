import { useMemo } from "react";
import { JsonViewer } from "./JsonViewer";
import { extractOutputsByIndex } from "./PerRowView";
import type { TaskDetailArtifact } from "../types";

interface AnnotationViewProps {
  artifacts: TaskDetailArtifact[];
  sourceRef: unknown;
}

interface AnnotationOutput {
  entities?: unknown;
  classifications?: unknown;
  relations?: unknown;
  json_structures?: unknown;
  [key: string]: unknown;
}

const KNOWN_FIELDS = new Set(["entities", "classifications", "relations", "json_structures"]);

export function AnnotationView({ artifacts, sourceRef }: AnnotationViewProps) {
  const outputs = useMemo(() => extractOutputsByIndex(artifacts), [artifacts]);
  const rowMeta = useMemo(() => extractRowMeta(sourceRef), [sourceRef]);

  if (outputs.size === 0) {
    return <p className="empty-detail">No annotation rows to render.</p>;
  }

  const rowIndices = Array.from(outputs.keys()).sort((a, b) => a - b);

  return (
    <div className="annotation-view">
      {rowIndices.map((idx) => {
        const output = outputs.get(idx) as AnnotationOutput;
        const meta = rowMeta.get(idx);
        return (
          <div className="annotation-row" key={idx}>
            <div className="annotation-row-header">
              <span className="annotation-row-index">#{idx}</span>
              {meta?.row_id ? <small>{meta.row_id}</small> : null}
              {meta?.source_id ? <small>· {meta.source_id}</small> : null}
            </div>
            {meta?.input ? <RowInput input={meta.input} /> : null}
            <AnnotationOutputView output={output} />
          </div>
        );
      })}
    </div>
  );
}

function AnnotationOutputView({ output }: { output: AnnotationOutput }) {
  const unknownFields = Object.keys(output).filter((k) => !KNOWN_FIELDS.has(k));
  const isEmpty =
    !hasContent(output.entities) &&
    !hasContent(output.classifications) &&
    !hasContent(output.relations) &&
    !hasContent(output.json_structures) &&
    unknownFields.length === 0;

  if (isEmpty) {
    return <p className="annotation-empty">No annotations.</p>;
  }

  return (
    <div className="annotation-sections">
      <EntitiesSection entities={output.entities} />
      <ClassificationsSection classifications={output.classifications} />
      <RelationsSection relations={output.relations} />
      <JsonStructuresSection structures={output.json_structures} />
      {unknownFields.length > 0 ? (
        <div className="annotation-section">
          <h5>Other fields</h5>
          <JsonViewer value={pick(output, unknownFields)} />
        </div>
      ) : null}
    </div>
  );
}

const ENTITY_COLORS: Record<string, string> = {
  person: "#5fb3ff",
  organization: "#ff9c5f",
  project: "#b18cff",
  document: "#7ed3a0",
  time: "#ffd166",
  number: "#f0908b",
  event: "#83d9d8",
  location: "#ffa3c5",
  technology: "#a8d957",
  entity: "#999999",
};

function EntitiesSection({ entities }: { entities: unknown }) {
  if (!isObjectRecord(entities)) return null;
  const items = Object.entries(entities).filter(([, v]) => Array.isArray(v) && v.length > 0);
  if (items.length === 0) return null;
  return (
    <div className="annotation-section">
      <h5>Entities ({items.reduce((sum, [, v]) => sum + (v as unknown[]).length, 0)})</h5>
      <div className="entity-groups">
        {items.map(([type, spans]) => (
          <div className="entity-group" key={type}>
            <span
              className="entity-type-label"
              style={{ background: ENTITY_COLORS[type] ?? "#888", color: "#0a121a" }}
            >
              {type}
            </span>
            <div className="entity-spans">
              {(spans as unknown[]).map((span, i) => (
                <span className="entity-span-chip" key={`${type}-${i}`}>
                  {typeof span === "string" ? span : JSON.stringify(span)}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface ClassificationItem {
  task?: string;
  final_label?: string;
  label_options?: string[];
}

function ClassificationsSection({ classifications }: { classifications: unknown }) {
  if (!Array.isArray(classifications) || classifications.length === 0) return null;
  return (
    <div className="annotation-section">
      <h5>Classifications ({classifications.length})</h5>
      <div className="classification-list">
        {(classifications as ClassificationItem[]).map((c, i) => (
          <div className="classification-card" key={i}>
            <span className="classification-task">{c.task ?? "—"}</span>
            <span className="classification-label">{c.final_label ?? "—"}</span>
            {Array.isArray(c.label_options) && c.label_options.length > 0 ? (
              <span className="classification-options">
                from{" "}
                {c.label_options.map((opt, j) => (
                  <span
                    key={j}
                    className={opt === c.final_label ? "option selected" : "option"}
                  >
                    {opt}
                  </span>
                ))}
              </span>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function RelationsSection({ relations }: { relations: unknown }) {
  if (!Array.isArray(relations) || relations.length === 0) return null;
  return (
    <div className="annotation-section">
      <h5>Relations ({relations.length})</h5>
      <div className="relation-list">
        {(relations as unknown[]).map((rel, i) => {
          // Each relation is { <type>: { head, tail, ... } }
          if (!isObjectRecord(rel)) return null;
          const entries = Object.entries(rel);
          if (entries.length === 0) return null;
          const [type, payload] = entries[0];
          if (!isObjectRecord(payload)) {
            return (
              <div className="relation-row" key={i}>
                <span className="relation-type">{type}</span>
                <code>{JSON.stringify(payload)}</code>
              </div>
            );
          }
          const head = String(payload.head ?? "");
          const tail = String(payload.tail ?? "");
          return (
            <div className="relation-row" key={i}>
              <span className="relation-head">{head}</span>
              <span className="relation-arrow">→</span>
              <span className="relation-tail">{tail}</span>
              <span className="relation-type">{type}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface PhraseItem {
  text?: string;
  start?: number;
  end?: number;
}

function JsonStructuresSection({ structures }: { structures: unknown }) {
  if (!isObjectRecord(structures)) return null;
  const items = Object.entries(structures).filter(
    ([, v]) => Array.isArray(v) && v.length > 0,
  );
  if (items.length === 0) return null;
  return (
    <div className="annotation-section">
      <h5>JSON Structures ({items.length} types)</h5>
      <div className="json-structures-list">
        {items.map(([type, phrases]) => (
          <div className="json-structure-group" key={type}>
            <span className="json-structure-type">{type}</span>
            <div className="phrase-chips">
              {(phrases as PhraseItem[]).map((p, i) => (
                <span className="phrase-chip" key={i} title={`${p.start}-${p.end}`}>
                  {p.text ?? JSON.stringify(p)}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RowInput({ input }: { input: unknown }) {
  if (typeof input === "string") {
    return <p className="annotation-row-input">{input}</p>;
  }
  if (isObjectRecord(input) && typeof input.text === "string") {
    return <p className="annotation-row-input">{input.text}</p>;
  }
  return null;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasContent(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value as object).length > 0;
  return true;
}

function pick(obj: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of keys) out[k] = obj[k];
  return out;
}

function extractRowMeta(sourceRef: unknown): Map<number, { row_id?: string; source_id?: string; input?: unknown }> {
  const out = new Map<number, { row_id?: string; source_id?: string; input?: unknown }>();
  if (!isObjectRecord(sourceRef)) return out;
  const payload = sourceRef.payload;
  if (!isObjectRecord(payload)) return out;
  const rows = payload.rows;
  if (!Array.isArray(rows)) return out;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    if (!isObjectRecord(r)) continue;
    const idx = typeof r.row_index === "number" ? r.row_index : i;
    out.set(idx, {
      row_id: typeof r.row_id === "string" ? r.row_id : undefined,
      source_id: typeof r.source_id === "string" ? r.source_id : undefined,
      input: r.input,
    });
  }
  return out;
}
