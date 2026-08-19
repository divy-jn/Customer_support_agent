"use client";
import { useState, useEffect } from "react";
import { API_BASE } from "../lib/api";

const PREDEFINED_MODELS = [
  "gpt-oss:20b-cloud",
  "gpt-oss:120b-cloud",
  "gemma4:cloud",
  "gemma4:31b-cloud",
  "nemotron-3-nano:30b-cloud",
  "minimax-m3:cloud",
  "nemotron-3-super:cloud"
];

export function DynamicModelSettings() {
  const [settings, setSettings] = useState({
    llm_base_url: "",
    small_model: "",
    large_model: "",
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/admin/llm-settings`)
      .then((res) => res.json())
      .then((data) => {
        setSettings(data);
        setIsLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load LLM settings", err);
        setIsLoading(false);
      });
  }, []);

  const handleChange = (field, value) => {
    setSettings((prev) => ({ ...prev, [field]: value }));
    setSaveStatus(null); // Clear status on edit
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
          <div className="h-10 w-full bg-[var(--bg-input)] rounded"></div>
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
            Dynamic Model Selection
            <span className="bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded-full text-[10px] font-bold">
              LIVE
            </span>
          </h2>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            Changes made here take effect instantly without restarting the server.
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
            Cloud Provider Base URL
          </label>
          <input
            type="text"
            className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded p-2.5 text-sm font-mono focus:border-[var(--accent)] focus:outline-none transition-colors"
            value={settings.llm_base_url}
            onChange={(e) => handleChange("llm_base_url", e.target.value)}
            placeholder="https://api.openai.com/v1"
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div>
            <label className="block text-xs font-medium text-[var(--text-muted)] mb-1">
              Small Model (Fast Router / Escalation)
            </label>
            <div className="relative">
              <input
                type="text"
                list="models-list"
                className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded p-2.5 text-sm font-mono focus:border-[var(--accent)] focus:outline-none transition-colors"
                value={settings.small_model}
                onChange={(e) => handleChange("small_model", e.target.value)}
                placeholder="e.g. glm-4"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--text-muted)] mb-1">
              Large Model (RAG / DB Tools / Web)
            </label>
            <div className="relative">
              <input
                type="text"
                list="models-list"
                className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded p-2.5 text-sm font-mono focus:border-[var(--accent)] focus:outline-none transition-colors"
                value={settings.large_model}
                onChange={(e) => handleChange("large_model", e.target.value)}
                placeholder="e.g. nemotron-4-340b-instruct"
              />
            </div>
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
