import type { PublicDemoScenario } from "../api";

export interface ScenarioPickerProps {
  scenarios: readonly PublicDemoScenario[];
  value: string;
  onChange: (scenarioId: string) => void;
}

export function ScenarioPicker({ scenarios, value, onChange }: ScenarioPickerProps) {
  const selected = scenarios.find((scenario) => scenario.scenario_id === value);

  return (
    <fieldset className="scenario-picker">
      <legend>Offline engineering demonstrations</legend>
      <label htmlFor="scenario">Offline demo scenario</label>
      <select id="scenario" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Custom fixture question</option>
        {scenarios.map((scenario) => (
          <option key={scenario.scenario_id} value={scenario.scenario_id}>
            {scenario.label}
          </option>
        ))}
      </select>
      {selected === undefined ? null : (
        <div className="scenario-picker__details">
          <p>{selected.description}</p>
          <p className="scenario-picker__limitation">{selected.engineering_limitation}</p>
        </div>
      )}
    </fieldset>
  );
}
