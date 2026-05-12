import { useEffect, useState } from "react";
import { createDocument, createDocumentVersion, fetchDocumentDetail, fetchDocuments, fetchGuidelines } from "../api";
import type { Guideline } from "../api";
import type { AnnotationDocument, AnnotationDocumentVersion, DocumentDetail } from "../types";

interface DocumentsPanelProps {
  storeKey: string | null;
}

export function DocumentsPanel({ storeKey }: DocumentsPanelProps) {
  const [guidelines, setGuidelines] = useState<Guideline[]>([]);
  const [documents, setDocuments] = useState<AnnotationDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedDocId, setExpandedDocId] = useState<string | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<DocumentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showCreateDoc, setShowCreateDoc] = useState(false);
  const [showAddVersion, setShowAddVersion] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [docTitle, setDocTitle] = useState("");
  const [docDescription, setDocDescription] = useState("");
  const [docCreatedBy, setDocCreatedBy] = useState("operator");

  const [verLabel, setVerLabel] = useState("v1");
  const [verContent, setVerContent] = useState("");
  const [verChangelog, setVerChangelog] = useState("");
  const [verCreatedBy, setVerCreatedBy] = useState("operator");

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([fetchDocuments(storeKey), fetchGuidelines(storeKey)])
      .then(([docSnap, guideSnap]) => {
        if (!active) return;
        setDocuments(docSnap.documents);
        setGuidelines(guideSnap.guidelines);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load documents");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [storeKey]);

  function toggleDoc(docId: string) {
    if (expandedDocId === docId) {
      setExpandedDocId(null);
      setExpandedDetail(null);
      return;
    }
    setExpandedDocId(docId);
    setExpandedDetail(null);
    setDetailLoading(true);
    fetchDocumentDetail(docId, storeKey)
      .then((detail) => {
        setExpandedDetail(detail);
      })
      .catch(() => {
        setExpandedDetail(null);
      })
      .finally(() => {
        setDetailLoading(false);
      });
  }

  async function submitCreateDoc() {
    setSaving(true);
    setSaveError(null);
    try {
      const doc = await createDocument({ title: docTitle, description: docDescription, created_by: docCreatedBy }, storeKey);
      setDocuments((prev) => [...prev, doc]);
      setDocTitle("");
      setDocDescription("");
      setShowCreateDoc(false);
    } catch (reason: unknown) {
      setSaveError(reason instanceof Error ? reason.message : "Unable to create document");
    } finally {
      setSaving(false);
    }
  }

  async function submitAddVersion() {
    if (!expandedDocId) return;
    setSaving(true);
    setSaveError(null);
    try {
      const ver = await createDocumentVersion(
        expandedDocId,
        { version: verLabel, content: verContent, changelog: verChangelog, created_by: verCreatedBy },
        storeKey,
      );
      setExpandedDetail((prev) =>
        prev ? { ...prev, versions: [...prev.versions, ver] } : prev,
      );
      setVerLabel("v1");
      setVerContent("");
      setVerChangelog("");
      setShowAddVersion(false);
    } catch (reason: unknown) {
      setSaveError(reason instanceof Error ? reason.message : "Unable to add version");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Annotation Documents</h2>
        <button className="primary-button" type="button" onClick={() => setShowCreateDoc((v) => !v)}>
          {showCreateDoc ? "Cancel" : "New Document"}
        </button>
      </div>

      {showCreateDoc ? (
        <div className="form-card">
          <h3>Create Document</h3>
          <label>
            Title
            <input type="text" value={docTitle} onChange={(e) => setDocTitle(e.target.value)} />
          </label>
          <label>
            Description
            <input type="text" value={docDescription} onChange={(e) => setDocDescription(e.target.value)} />
          </label>
          <label>
            Created by
            <input type="text" value={docCreatedBy} onChange={(e) => setDocCreatedBy(e.target.value)} />
          </label>
          {saveError ? <div className="drawer-error">{saveError}</div> : null}
          <button className="primary-button" type="button" disabled={saving || !docTitle.trim()} onClick={submitCreateDoc}>
            {saving ? "Saving" : "Create"}
          </button>
        </div>
      ) : null}

      {loading ? <div className="drawer-state">Loading documents</div> : null}
      {error ? <div className="drawer-error">{error}</div> : null}

      {guidelines.length > 0 ? (
        <div className="guideline-section">
          <h3 className="guideline-section-title">Annotation Guidelines</h3>
          {guidelines.map((g) => (
            <GuidelineCard key={g.label} guideline={g} />
          ))}
        </div>
      ) : null}

      {!loading && documents.length === 0 && guidelines.length === 0 ? (
        <p className="empty-detail">No annotation documents yet.</p>
      ) : null}

      {documents.map((doc) => (
        <div key={doc.document_id} className="detail-section">
          <button
            className="view-tab"
            type="button"
            onClick={() => toggleDoc(doc.document_id)}
          >
            {doc.title} <small>({doc.document_id})</small>
          </button>
          <p>{doc.description}</p>
          <small>Created by {doc.created_by} at {doc.created_at}</small>

          {expandedDocId === doc.document_id ? (
            <div className="detail-sections">
              {detailLoading ? <div className="drawer-state">Loading versions</div> : null}

              {expandedDetail ? (
                <>
                  {expandedDetail.versions.length === 0 ? (
                    <p className="empty-detail">No versions yet.</p>
                  ) : (
                    expandedDetail.versions.map((ver) => (
                      <VersionCard key={ver.version_id} ver={ver} />
                    ))
                  )}

                  <button className="primary-button" type="button" onClick={() => setShowAddVersion((v) => !v)}>
                    {showAddVersion ? "Cancel" : "Add Version"}
                  </button>

                  {showAddVersion ? (
                    <div className="form-card">
                      <h4>Add Version to {doc.title}</h4>
                      <label>
                        Version label
                        <input type="text" value={verLabel} onChange={(e) => setVerLabel(e.target.value)} />
                      </label>
                      <label>
                        Content (markdown guideline)
                        <textarea rows={8} value={verContent} onChange={(e) => setVerContent(e.target.value)} />
                      </label>
                      <label>
                        Changelog
                        <input type="text" value={verChangelog} onChange={(e) => setVerChangelog(e.target.value)} />
                      </label>
                      <label>
                        Created by
                        <input type="text" value={verCreatedBy} onChange={(e) => setVerCreatedBy(e.target.value)} />
                      </label>
                      {saveError ? <div className="drawer-error">{saveError}</div> : null}
                      <button
                        className="primary-button"
                        type="button"
                        disabled={saving || !verLabel.trim() || !verContent.trim()}
                        onClick={submitAddVersion}
                      >
                        {saving ? "Saving" : "Add Version"}
                      </button>
                    </div>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function GuidelineCard({ guideline }: { guideline: Guideline }) {
  const [expanded, setExpanded] = useState(false);
  const labelMap: Record<string, string> = {
    primary: "Primary Guide",
    source_overrides: "Source Overrides",
  };
  const title = labelMap[guideline.label] ?? guideline.label;
  return (
    <details
      className="timeline-item guideline-card"
      open={expanded}
      onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}
    >
      <summary>
        <span>{title}</span>
        <small>{guideline.filename}{!guideline.exists ? " · file not found" : ""}</small>
      </summary>
      {expanded ? (
        guideline.content ? (
          <pre className="guideline-content">{guideline.content}</pre>
        ) : (
          <p className="empty-detail">File not found: {guideline.path}</p>
        )
      ) : null}
    </details>
  );
}

function VersionCard({ ver }: { ver: AnnotationDocumentVersion }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details className="timeline-item" open={expanded} onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}>
      <summary>
        <span>{ver.version}</span>
        <small>{ver.changelog || "no changelog"} &mdash; {ver.created_by} at {ver.created_at}</small>
      </summary>
      {expanded ? <pre className="json-block">{ver.content}</pre> : null}
    </details>
  );
}
