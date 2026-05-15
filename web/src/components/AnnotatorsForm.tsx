import { useEffect, useState } from "react";
import { fetchAnnotatorsConfig, saveAnnotatorsConfig } from "../api";

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

// Stages the runtime resolves via llm_profiles.yaml `targets`. `fallback` is
// the profile used when a primary stage call fails. Order matters for layout;
// any extra stages found in the loaded targets are appended after these.
const KNOWN_STAGES = ["annotation", "qc", "arbiter", "coordinator", "fallback"] as const;

export function AnnotatorsForm({ storeKey }: AnnotatorsFormProps) {
  const [profiles, setProfiles] = useState<string[]>([]);
  const [stageTargets, setStageTargets] = useState<Record<string, string>>({});
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
        setProfiles(snap.available_profiles);
        setStageTargets(snap.stage_targets ?? {});
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

  function updateStageTarget(stage: string, profile: string) {
    setStageTargets((current) => ({ ...current, [stage]: profile }));
  }

  async function submit() {
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const sampling: Record<string, Record<string, unknown>> = { ...otherSampling };
      sampling.qc = { ...qcSampling };
      const cleanTargets = Object.fromEntries(
        Object.entries(stageTargets).filter(([, v]) => typeof v === "string" && v.length > 0),
      );
      // Annotators block on disk is preserved by the backend when omitted here.
      await saveAnnotatorsConfig({ sampling, stage_targets: cleanTargets }, storeKey);
      setMessage("Saved");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to save");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="drawer-state">Loading annotators…</div>;

  const stages = Array.from(new Set([...KNOWN_STAGES, ...Object.keys(stageTargets)]));

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
        <h3>Stage Targets</h3>
        <p className="empty-detail" style={{ marginTop: "-4px" }}>
          The runtime resolves each pipeline stage to a profile via{" "}
          <code>llm_profiles.yaml</code> targets. <code>fallback</code> is used
          when a primary stage call fails. Edits here write to the workspace
          file and take effect on the next dispatch.
        </p>
        <div className="annotator-card-fields">
          {stages.map((stage) => (
            <label key={stage}>
              <span style={{ textTransform: "capitalize" }}>{stage}</span>
              <select
                value={stageTargets[stage] ?? ""}
                onChange={(e) => updateStageTarget(stage, e.target.value)}
              >
                <option value="">— unset —</option>
                {profiles.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
                {stageTargets[stage] && !profiles.includes(stageTargets[stage]) ? (
                  <option value={stageTargets[stage]}>
                    {stageTargets[stage]} (not in profiles)
                  </option>
                ) : null}
              </select>
            </label>
          ))}
        </div>
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
