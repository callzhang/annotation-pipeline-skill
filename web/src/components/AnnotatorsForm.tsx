import { useEffect, useState } from "react";
import { fetchAnnotatorsConfig, saveAnnotatorsConfig, type AnnotatorConfig } from "../api";

interface AnnotatorsFormProps {
  storeKey: string | null;
}

interface QcSampling {
  strategy: string;
  ratio: number;
  batch_size: number;
  threshold: number;
  require_all_batches_pass: boolean;
}

const defaultQcSampling: QcSampling = {
  strategy: "stratified",
  ratio: 1.0,
  batch_size: 10,
  threshold: 16,
  require_all_batches_pass: true,
};

function parseQcSampling(raw: Record<string, unknown> | undefined): QcSampling {
  if (!raw) return { ...defaultQcSampling };
  return {
    strategy: typeof raw.strategy === "string" ? raw.strategy : defaultQcSampling.strategy,
    ratio: typeof raw.ratio === "number" ? raw.ratio : defaultQcSampling.ratio,
    batch_size: typeof raw.batch_size === "number" ? raw.batch_size : defaultQcSampling.batch_size,
    threshold: typeof raw.threshold === "number" ? raw.threshold : defaultQcSampling.threshold,
    require_all_batches_pass:
      typeof raw.require_all_batches_pass === "boolean"
        ? raw.require_all_batches_pass
        : defaultQcSampling.require_all_batches_pass,
  };
}

export function AnnotatorsForm({ storeKey }: AnnotatorsFormProps) {
  const [annotators, setAnnotators] = useState<AnnotatorConfig[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [qcSampling, setQcSampling] = useState<QcSampling>(defaultQcSampling);
  const [otherSampling, setOtherSampling] = useState<Record<string, Record<string, unknown>>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchAnnotatorsConfig(storeKey)
      .then((snap) => {
        if (!active) return;
        setAnnotators(snap.annotators);
        setProfiles(snap.available_profiles);
        const { qc, ...rest } = snap.sampling;
        setQcSampling(parseQcSampling(qc as Record<string, unknown> | undefined));
        setOtherSampling(rest);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Unable to load annotators");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [storeKey]);

  function updateAnnotator(id: string, patch: Partial<AnnotatorConfig>) {
    setAnnotators((current) => current.map((a) => (a.id === id ? { ...a, ...patch } : a)));
  }

  async function submit() {
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const sampling: Record<string, Record<string, unknown>> = { ...otherSampling };
      sampling.qc = { ...qcSampling };
      await saveAnnotatorsConfig({ annotators, sampling }, storeKey);
      setMessage("Saved annotators.yaml");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to save annotators");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="drawer-state">Loading annotators…</div>;

  return (
    <div className="annotators-form">
      <div className="config-editor-header">
        <div>
          <h2>Annotation Agents</h2>
          <p>Choose the LLM profile that runs each annotation stage and tune QC sampling.</p>
        </div>
        <button className="primary-button" type="button" disabled={saving} onClick={submit}>
          {saving ? "Saving" : "Save"}
        </button>
      </div>

      {error ? <div className="drawer-error">{error}</div> : null}
      {message ? <div className="notice compact">{message}</div> : null}

      <section className="annotators-section">
        <h3>Stages</h3>
        {annotators.length === 0 ? (
          <p className="empty-detail">No annotators defined.</p>
        ) : (
          <div className="annotator-list">
            {annotators.map((a) => (
              <div className="annotator-card" key={a.id}>
                <div className="annotator-card-header">
                  <div>
                    <strong>{a.display_name || a.id}</strong>
                    <small>
                      {a.id} · target: <code>{a.provider_target || "—"}</code>
                    </small>
                  </div>
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={a.enabled}
                      onChange={(e) => updateAnnotator(a.id, { enabled: e.target.checked })}
                    />
                    Enabled
                  </label>
                </div>
                <div className="annotator-card-fields">
                  <label>
                    <span>Display name</span>
                    <input
                      type="text"
                      value={a.display_name}
                      onChange={(e) => updateAnnotator(a.id, { display_name: e.target.value })}
                    />
                  </label>
                  <label>
                    <span>LLM Profile</span>
                    <select
                      value={a.llm_profile || ""}
                      onChange={(e) => updateAnnotator(a.id, { llm_profile: e.target.value })}
                    >
                      <option value="">— select —</option>
                      {profiles.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                      {a.llm_profile && !profiles.includes(a.llm_profile) ? (
                        <option value={a.llm_profile}>{a.llm_profile} (not in profiles)</option>
                      ) : null}
                    </select>
                  </label>
                  <label>
                    <span>Provider target</span>
                    <select
                      value={a.provider_target}
                      onChange={(e) => updateAnnotator(a.id, { provider_target: e.target.value })}
                    >
                      <option value="annotation">annotation</option>
                      <option value="qc">qc</option>
                      <option value="coordinator">coordinator</option>
                    </select>
                  </label>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="annotators-section">
        <h3>QC Sampling</h3>
        <div className="annotator-card-fields">
          <label>
            <span>Strategy</span>
            <select
              value={qcSampling.strategy}
              onChange={(e) => setQcSampling({ ...qcSampling, strategy: e.target.value })}
            >
              <option value="stratified">stratified</option>
              <option value="random">random</option>
              <option value="sample_all">sample_all</option>
            </select>
          </label>
          <label>
            <span>Ratio (0–1)</span>
            <input
              type="number"
              step="0.01"
              min={0}
              max={1}
              value={qcSampling.ratio}
              onChange={(e) => setQcSampling({ ...qcSampling, ratio: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Batch size</span>
            <input
              type="number"
              min={1}
              value={qcSampling.batch_size}
              onChange={(e) => setQcSampling({ ...qcSampling, batch_size: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Pass threshold</span>
            <input
              type="number"
              min={0}
              value={qcSampling.threshold}
              onChange={(e) => setQcSampling({ ...qcSampling, threshold: Number(e.target.value) })}
            />
          </label>
          <label className="checkbox-row wide">
            <input
              type="checkbox"
              checked={qcSampling.require_all_batches_pass}
              onChange={(e) => setQcSampling({ ...qcSampling, require_all_batches_pass: e.target.checked })}
            />
            Require all batches to pass
          </label>
        </div>
      </section>
    </div>
  );
}
