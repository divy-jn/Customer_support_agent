"use client";

import { useState, useEffect } from "react";
import { SkeletonCard } from "../../components/LoadingSkeleton";
import EmptyState from "../../components/EmptyState";
import { useToast } from "../../components/Toast";
import { API_BASE } from "../../lib/api";

export default function KnowledgeBasePage() {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reingesting, setReingesting] = useState(false);
  const [selectedDoc, setSelectedDoc] = useState(null);
  const [docContent, setDocContent] = useState("");
  const { toast, ToastContainer } = useToast();

  const fetchDocs = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/admin/knowledge-base`);
      const data = await res.json();
      setDocs(data.documents || []);
    } catch (err) {
      console.error("Failed to fetch documents:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    (async () => {
      await fetchDocs();
    })();
  }, []);

  const handleReingest = async () => {
    setReingesting(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/admin/knowledge-base/reingest`, {
        method: "POST",
      });
      const data = await res.json();
      if (data.status === "success") {
        toast("Knowledge base successfully re-ingested into Pinecone!", "success");
        fetchDocs(); // Refresh the document list
      } else {
        toast("Failed to re-ingest: " + (data.error || "Unknown error"), "error");
      }
    } catch (err) {
      toast("Error triggering re-ingestion.", "error");
    } finally {
      setReingesting(false);
    }
  };

  const loadDocContent = async (filename) => {
    setSelectedDoc(filename);
    setDocContent("Loading...");
    try {
      const res = await fetch(`${API_BASE}/api/v1/admin/knowledge-base/${filename}`);
      const data = await res.json();
      setDocContent(data.content);
    } catch (err) {
      setDocContent("Failed to load document content.");
    }
  };

  // Simple markdown rendering for preview
  const renderSimpleMarkdown = (text) => {
    if (!text) return text;
    return text
      .replace(/^### (.*$)/gm, '<h3 style="font-size:14px;font-weight:700;color:var(--text-primary);margin:16px 0 8px;">$1</h3>')
      .replace(/^## (.*$)/gm, '<h2 style="font-size:16px;font-weight:700;color:var(--text-primary);margin:20px 0 10px;">$1</h2>')
      .replace(/^# (.*$)/gm, '<h1 style="font-size:20px;font-weight:700;color:var(--text-primary);margin:24px 0 12px;">$1</h1>')
      .replace(/\*\*(.*?)\*\*/g, '<strong style="color:var(--text-primary);">$1</strong>')
      .replace(/^[-•]\s+(.*)$/gm, '<div style="display:flex;gap:8px;margin:4px 0;"><span style="color:var(--accent);">•</span><span>$1</span></div>')
      .replace(/\n/g, '<br/>');
  };

  // Total KB size
  const totalSize = docs.reduce((acc, d) => acc + (d.size_bytes || 0), 0);

  if (loading) {
    return (
      <div className="p-8 max-w-6xl mx-auto page-enter">
        <SkeletonCard />
      </div>
    );
  }

  return (
    <div className="p-8 max-w-6xl mx-auto flex flex-col h-screen page-enter">
      <ToastContainer />

      <div className="mb-6 flex justify-between items-end shrink-0">
        <div>
          <h1 className="text-2xl font-bold">Knowledge Base Manager</h1>
          <p className="text-[var(--text-muted)] mt-1">
            {docs.length} documents · {(totalSize / 1024).toFixed(1)} KB total
          </p>
        </div>
        <button
          onClick={handleReingest}
          disabled={reingesting}
          className="btn-glow px-6 py-2.5 flex items-center gap-2 disabled:opacity-50"
          style={{ background: "linear-gradient(135deg, #ef4444, #f97316)" }}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className={`w-5 h-5 ${reingesting ? "animate-spin" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          {reingesting ? "Re-ingesting…" : "Re-ingest into Pinecone"}
        </button>
      </div>

      <div className="flex-1 flex gap-6 overflow-hidden pb-8">
        {/* Document List */}
        <div className="w-1/3 glass-card overflow-y-auto">
          <div className="p-4 border-b border-[var(--border)] bg-[#0f0f15] sticky top-0 z-10">
            <h3 className="font-semibold text-sm">Documents ({docs.length})</h3>
          </div>
          {docs.length === 0 ? (
            <EmptyState
              icon="📄"
              title="No documents"
              description="Add .md files to the knowledge_base directory."
            />
          ) : (
            <div className="divide-y divide-[var(--border)]">
              {docs.map((doc, idx) => (
                <button
                  key={idx}
                  onClick={() => loadDocContent(doc.filename)}
                  className={`w-full text-left p-4 hover:bg-[var(--bg-input)] transition-colors ${
                    selectedDoc === doc.filename
                      ? "bg-[var(--bg-input)] border-l-2 border-red-500"
                      : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-base">📝</span>
                    <p className="font-medium text-sm text-[var(--text-primary)] truncate">
                      {doc.filename}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-[var(--text-muted)] ml-7">
                    <span>{(doc.size_bytes / 1024).toFixed(1)} KB</span>
                    <span>&bull;</span>
                    <span>{new Date(doc.modified_at).toLocaleDateString()}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Document Preview */}
        <div className="flex-1 glass-card flex flex-col overflow-hidden">
          {selectedDoc ? (
            <>
              <div className="p-4 border-b border-[var(--border)] bg-[#0f0f15] flex items-center justify-between shrink-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm">📝</span>
                  <h3 className="font-mono text-sm text-[var(--accent)]">{selectedDoc}</h3>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(docContent);
                      toast("Content copied to clipboard!", "success");
                    }}
                    className="btn-secondary px-3 py-1 text-[10px]"
                  >
                    Copy
                  </button>
                </div>
              </div>
              <div className="flex-1 p-6 overflow-y-auto text-sm text-[var(--text-secondary)] leading-relaxed">
                {docContent === "Loading..." ? (
                  <div className="flex items-center justify-center h-full">
                    <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
                  </div>
                ) : (
                  <div dangerouslySetInnerHTML={{ __html: renderSimpleMarkdown(docContent) }} />
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <EmptyState
                icon="📖"
                title="Select a document"
                description="Click on a document to preview its contents with markdown rendering."
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
