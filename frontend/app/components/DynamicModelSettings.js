"use client";
import { useState, useEffect } from "react";
import { API_BASE } from "../lib/api";

const PREDEFINED_MODELS = [
  "gemini-3.8-flash",
  "gemini-2.5-flash",
  "gemini-2.0-flash",
  "gemini-1.5-flash",
  "gemini-1.5-pro",
];

export function DynamicModelSettings() {
  const [settings, setSettings] = useState({
    model: "gemini-3.8-flash",
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/admin/llm-settings`)
      .then((res) => res.json())
      .then((data) => {
        if (data && data.model) {
          setSettings({ model: data.model });
        }
        setIsLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load LLM settings", err);
        setIsLoading(false);
      });
  }, []);

  const handleChange = (field, value) => {
    setSettings((prev) => ({ ...prev, [field]: value }));
    setSaveStatus(null);
  };

  const handleSave = async () => {
    setIsSaving(true);
    setSaveStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/admin/llm-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      if (res.ok) {
        setSaveStatus("success");
        setTimeout(() => setSaveStatus(null), 3000);
      } else {
        setSaveStatus("error");
      }
    } catch (err) {
      console.error(err);
      setSaveStatus("error");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="glass-card p-6 animate-pulse">
        <div className="h-5 w-48 bg-[var(--border)] rounded mb-4"></div>
        <div className="space-y-4">
          <div className="h-10 w-full bg-[var(--bg-input)] rounded"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="glass-card p-6 border-l-2 border-emerald-500">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-2">
            Vertex AI Model Selection
            <span className="bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded-full text-[10px] font-bold">
              LIVE
            </span>
          </h2>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            Configure the primary model on Google Cloud Vertex AI. Takes effect immediately.
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="bg-[var(--accent)] text-[var(--bg-primary)] px-4 py-2 rounded-lg text-sm font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {isSaving ? "Saving..." : saveStatus === "success" ? "Saved!" : "Save Changes"}
        </button>
      </div>

      <div className="space-y-5">
        <div>
          <label className="block text-xs font-medium text-[var(--text-muted)] mb-1">
            Primary Model Name
          </label>
          <div className="relative">
            <input
              type="text"
              list="models-list"
              className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded p-2.5 text-sm font-mono focus:border-[var(--accent)] focus:outline-none transition-colors"
              value={settings.model}
              onChange={(e) => handleChange("model", e.target.value)}
              placeholder="gemini-3.8-flash"
            />
          </div>
        </div>

        <datalist id="models-list">
          {PREDEFINED_MODELS.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>

        {saveStatus === "error" && (
          <p className="text-red-400 text-xs mt-2">Failed to save settings. Check backend logs.</p>
        )}
      </div>
    </div>
  );
}
