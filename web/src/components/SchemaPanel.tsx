import { useEffect, useState } from "react";
import { fetchProjectSchema } from "../api";
import { JsonViewer } from "./JsonViewer";

interface SchemaPanelProps {
  storeKey: string | null;
}

export function SchemaPanel({ storeKey }: SchemaPanelProps) {
  const [schema, setSchema] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchProjectSchema(storeKey)
      .then((result) => {
        if (!active) return;
        setSchema(result.schema);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load schema");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [storeKey]);

  if (loading) return <section className="work-panel">Loading schema</section>;

  return (
    <section className="runtime-panel" aria-label="Output schema">
      <div className="runtime-header">
        <div>
          <h2>Output Schema</h2>
          <p>Project-level JSON Schema used for annotation and QC validation</p>
        </div>
      </div>
      {error ? <div className="notice compact error">{error}</div> : null}
      {!schema ? (
        <div className="notice compact">No <code>output_schema.json</code> found for this project.</div>
      ) : (
        <div className="schema-viewer">
          <JsonViewer value={schema} />
        </div>
      )}
    </section>
  );
}
