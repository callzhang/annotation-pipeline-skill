import { useEffect, useState } from "react";
import { fetchConfigSnapshot, saveConfigFile } from "../api";
import type { ConfigFile } from "../types";

const configHints: Record<string, string> = {
  "annotation_rules.yaml": "Rules that guide how annotators should label the data.",
  "annotators.yaml": "Annotator capability profiles, modalities, annotation types, and renderer hooks.",
  "llm_profiles.yaml": "Subagent provider profiles and stage target bindings for Annotation, QC, and coordinator agents.",
  "workflow.yaml": "Workflow stages, target bindings, and Human Review policy.",
  "external_tasks.yaml": "External task API pull, submit, and status integration settings.",
  "callbacks.yaml": "Callback endpoints for status and submit notifications.",
};

export function ConfigPanel() {
  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchConfigSnapshot()
      .then((snapshot) => {
        if (!active) return;
        setFiles(snapshot.files);
        const first = snapshot.files[0] ?? null;
        setSelectedId(first?.id ?? null);
        setDraft(first?.content ?? "");
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setMessage(reason instanceof Error ? reason.message : "Unable to load configuration");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const selected = files.find((file) => file.id === selectedId) ?? null;

  function selectFile(file: ConfigFile) {
    setSelectedId(file.id);
    setDraft(file.content);
    setMessage(null);
  }

  async function saveSelected() {
    if (!selected) return;
    setSaving(true);
    setMessage(null);
    try {
      await saveConfigFile(selected.id, draft);
      setFiles((current) => current.map((file) => (file.id === selected.id ? { ...file, content: draft, exists: true } : file)));
      setMessage(`Saved ${selected.id}`);
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "Unable to save configuration");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <section className="work-panel">Loading configuration</section>;

  return (
    <section className="work-panel config-layout" aria-label="Configuration">
      <aside className="config-list">
        {files.map((file) => (
          <button
            className={file.id === selectedId ? "config-list-item selected" : "config-list-item"}
            key={file.id}
            type="button"
            onClick={() => selectFile(file)}
          >
            <span>{file.title}</span>
            <small>{file.id}</small>
          </button>
        ))}
      </aside>

      <div className="config-editor">
        {selected ? (
          <>
            <div className="config-editor-header">
              <div>
                <h2>{selected.title}</h2>
                <p>{configHints[selected.id] ?? selected.path}</p>
              </div>
              <button className="primary-button" type="button" disabled={saving} onClick={saveSelected}>
                {saving ? "Saving" : "Save"}
              </button>
            </div>
            {message ? <div className="notice compact">{message}</div> : null}
            <textarea
              className="config-textarea"
              spellCheck={false}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
          </>
        ) : (
          <div>No configuration file selected.</div>
        )}
      </div>
    </section>
  );
}
