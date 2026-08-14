import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import type { PublicDemoScenario } from "./api";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("trace root missing");

const sourceManifestId = root.dataset.sourceManifestId ?? "";
const corpusManifestId = root.dataset.corpusManifestId ?? "";
const profile = root.dataset.profile === "fixture" ? "fixture" : "real";

function publicScenarios(value: string | undefined): PublicDemoScenario[] {
  if (value === undefined) return [];
  try {
    const decoded: unknown = JSON.parse(value);
    if (!Array.isArray(decoded)) return [];
    return decoded.filter((item): item is PublicDemoScenario => {
      if (typeof item !== "object" || item === null || Array.isArray(item)) return false;
      const record = item as Record<string, unknown>;
      return Object.keys(record).length === 5
        && typeof record.scenario_id === "string"
        && ["l1_answered", "l2_answered", "evidence_refused", "verifier_recovered"].includes(record.scenario_id)
        && typeof record.label === "string"
        && typeof record.description === "string"
        && (record.task_level === "L1" || record.task_level === "L2")
        && typeof record.engineering_limitation === "string";
    });
  } catch {
    return [];
  }
}

createRoot(root).render(
  <StrictMode>
    <App sourceManifestId={sourceManifestId} corpusManifestId={corpusManifestId} profile={profile} demoScenarios={profile === "fixture" ? publicScenarios(root.dataset.demoScenarios) : []} />
  </StrictMode>,
);
