import { listPanels } from "../registry";

import type { PanelProps, PanelSlot as PanelSlotName } from "../registry";

type PanelSlotProps = PanelProps & { name: PanelSlotName };

/** Renders the panels registered for a slot, in registration order. */
export function PanelSlot({ name, view, results }: PanelSlotProps) {
  return (
    <>
      {listPanels(name).map((Panel, index) => (
        // The registry is fixed once the page loads, so the index is a stable key.
        <Panel key={index} view={view} results={results} />
      ))}
    </>
  );
}
