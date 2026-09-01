import HeroIntro from "./HeroIntro";
import MetricsPanel from "./MetricsPanel";
import AttentionPanel from "./AttentionPanel";
import RecentActivityPreview from "./RecentActivityPreview";
import SafetyBoundary from "./SafetyBoundary";

// "What is happening across the recovery system?" — system-wide state,
// right now. No charts, no single-case detail — those belong to Analytics
// and Live Agent respectively.
//
// Layout order is deliberate: the Hero stays compact (a short narrative
// strip, not a full-viewport section) so the real, live MetricsPanel is
// visible without scrolling past it — the product story introduces the
// system, it doesn't delay the operational data.
export default function OverviewView() {
  return (
    <div className="space-y-5">
      <HeroIntro />
      <MetricsPanel />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <AttentionPanel />
        <RecentActivityPreview />
      </div>
      <SafetyBoundary />
    </div>
  );
}