import type { ClassifiedAction, PreviewItem } from "../api/types";

interface Props {
  items: PreviewItem[];
  actions: ClassifiedAction[];
  planStatus: string | null;
  selected: number | null;
  onSelect: (index: number) => void;
}

const OUTCOME: Record<string, string> = {
  completed: "The last plan was applied and verified, so there is nothing pending. See the Run tab for the result.",
  rolled_back: "The last plan was rolled back, so nothing was kept. See the Run tab for why.",
  rejected: "The last plan was rejected, so nothing was changed.",
};

/** What each plan step would write, as a line diff. Nothing is applied until you approve. */
export function DiffView({ items, actions, planStatus, selected, onSelect }: Props) {
  if (items.length === 0) {
    return (
      <div className="empty">
        {(planStatus && OUTCOME[planStatus]) || "No plan yet. Generate a recovery plan to preview exactly what would change."}
      </div>
    );
  }
  return (
    <div className="pane-body">
      <div className="callout info">
        Preview only. Nothing has been changed. Existing values stay hidden; only the value being written is shown.
      </div>
      {items.map((item) => {
        const action = actions[item.index];
        return (
          <div
            key={item.index}
            className={`diff-item${selected === item.index ? " sel" : ""}`}
            onClick={() => onSelect(item.index)}
          >
            <div className="diff-title">
              <span>
                Step {item.index + 1}: {item.summary || item.type}
              </span>
              {item.path && <span className="muted">{item.path}</span>}
              {item.created && <span className="badge info">new file</span>}
              {action && <span className={`badge ${action.risk}`}>{action.risk} risk</span>}
            </div>
            {item.diff.length === 0 && (
              <div className="pad">
                <div>{item.summary || "No file preview for this step."}</div>
                {item.needs_network && <div className="muted" style={{ marginTop: 4 }}>Runs with network access (the sandbox has none otherwise).</div>}
                {item.warning && <div className="callout bad" style={{ margin: "8px 0 0" }}>{item.warning}</div>}
              </div>
            )}
            {item.diff.length > 0 && item.warning && <div className="callout bad" style={{ margin: 8 }}>{item.warning}</div>}
            {item.diff.map((line, i) => (
              <div key={i} className={`diff-line ${line.op}`}>
                <span className="g">{line.op === "add" ? "+" : line.op === "del" ? "−" : ""}</span>
                <span>{line.text || " "}</span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
