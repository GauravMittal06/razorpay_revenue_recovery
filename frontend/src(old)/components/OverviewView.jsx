import MetricsPanel from "./MetricsPanel";
import AttentionPanel from "./AttentionPanel";
import RecentActivityPreview from "./RecentActivityPreview";

// "What is happening across the recovery system?" — system-wide, state,
// right now. No charts, no single-case detail — those belong to Analytics
// and Live Agent respectively.
export default function OverviewView() {
  return (
    <div className="space-y-4">
      <MetricsPanel />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <AttentionPanel />
        <RecentActivityPreview />
      </div>
    </div>
  );
}