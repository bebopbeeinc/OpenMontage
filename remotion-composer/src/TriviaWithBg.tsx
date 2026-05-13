import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/Montserrat";
import type { WordCaption } from "./components/CaptionOverlay";

const { fontFamily } = loadFont("normal", { weights: ["900"] });

export type TriviaMode = "Facts" | "Choices";

export interface ChoicesOption {
  label: string;
  revealAtSec: number;
}

export interface TriviaWithBgProps {
  videoSrc: string;
  words: WordCaption[];
  darkOverlay?: number;
  highlightColor?: string;
  baseColor?: string;
  fontSize?: number;
  showFactsOverlay?: boolean;
  mode?: TriviaMode;
  options?: ChoicesOption[];
  // Skip word-level captions whose midpoint falls within this window (ms).
  // For Choices mode the question + option list shouldn't be captioned.
  suppressCaptionsWindowMs?: [number, number] | null;
  // Resolution → CTA boundary. The first word of `ctaText` is matched against
  // the transcript (only past `ctaNominalStartMs - tolerance`) to find the
  // actual CTA-start position; `buildPages` then forces a new caption page to
  // begin at that word. Without this hint the resolution VO ("Floating duo")
  // and the CTA ("Lock yours in first") greedily merge into a single page
  // when there's no audio gap between them.
  ctaText?: string | null;
  ctaNominalStartMs?: number | null;
}

const ProgressBar: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = 180; // 6 seconds visible

  // Smooth 0→100% fill
  const progress = interpolate(frame, [0, totalFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Spring-in / Spring-out scale-Y
  const entry = spring({
    frame,
    fps,
    config: { damping: 9, stiffness: 220, mass: 1 },
    durationInFrames: 12,
  });
  const entryScaleY = interpolate(entry, [0, 1], [0, 1]);
  const exitFrame = Math.max(0, frame - (totalFrames - 12));
  const exit = spring({
    frame: exitFrame,
    fps,
    config: { damping: 9, stiffness: 220, mass: 1 },
    durationInFrames: 12,
  });
  const exitScaleY = interpolate(exit, [0, 1], [1, 0]);
  const scaleY = exitFrame > 0 ? exitScaleY : entryScaleY;

  // Bomb-fuse color zones: green safe → yellow caution → red danger
  const fillColor =
    progress < 0.4 ? "#58CC02" :
    progress < 0.75 ? "#FFD60A" :
    "#EF4444";
  const isDanger = progress >= 0.75;

  // Continuous shake that grows with progress; goes violent in last 1s
  const baseShakeAmp = progress * 4;
  const baseShake = Math.sin(frame * 0.6) * baseShakeAmp;
  const urgentStart = totalFrames - 30; // last 1s
  const urgentFrame = Math.max(0, frame - urgentStart);
  const urgent = urgentFrame > 0 ? (Math.sin(urgentFrame * 0.7) + 1) / 2 : 0;
  const violentShake =
    urgentFrame > 0 ? Math.sin(urgentFrame * 1.8) * (8 + progress * 10) : 0;
  const shakeX = baseShake + violentShake;
  const shakeY = urgentFrame > 0 ? Math.cos(urgentFrame * 1.6) * 3 : 0;

  // Glow intensifies and shifts color in danger zone
  const glowSize = 20 + progress * 40 + (urgentFrame > 0 ? urgentFrame * 1.5 : 0);
  const glowColor = isDanger ? "#EF4444" : (progress > 0.4 ? "#FFD60A" : "#58CC02");

  // Final flash: white-out at the very end (last 0.4s)
  const flashFrame = Math.max(0, frame - (totalFrames - 12));
  const flashOpacity =
    flashFrame > 0
      ? interpolate(flashFrame, [0, 4, 12], [0, 0.85, 0], {
          extrapolateRight: "clamp",
        })
      : 0;

  // Hot leading-edge sparkle
  const sparkPulse = (Math.sin(frame * 0.9) + 1) / 2;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {/* Final white-out flash at peak */}
      {flashOpacity > 0 && (
        <AbsoluteFill style={{ background: "white", opacity: flashOpacity }} />
      )}
      <div
        style={{
          position: "absolute",
          top: 80,
          left: 80,
          right: 80,
          height: 28,
          borderRadius: 14,
          background: "rgba(0, 0, 0, 0.6)",
          transform: `translate(${shakeX}px, ${shakeY}px) scaleY(${scaleY})`,
          boxShadow: `0 0 ${glowSize}px ${glowColor}, 0 0 ${glowSize * 0.5}px ${glowColor}`,
          border: isDanger
            ? "3px solid rgba(239, 68, 68, 0.9)"
            : "2px solid rgba(255, 255, 255, 0.2)",
          overflow: "visible",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${progress * 100}%`,
            background: `linear-gradient(90deg, ${fillColor}AA 0%, ${fillColor} 65%, ${isDanger ? "#FFFFFF" : "#FFE968"} 100%)`,
            boxShadow: `0 0 ${20 + sparkPulse * 25 + urgent * 30}px ${fillColor}`,
            borderRadius: 14,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

const FactsOverlay: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Body starts at global t=3s in the assembled video. This Sequence is
  // mounted from frame=90 (3s) so frame here is body-relative.
  // - Check button slams in at body+1s (frame 30)
  // - X button slams in at body+1.15s (frame ~34)
  // - Stopwatch rotates in at body+2.5s (frame 75)
  const checkSpring = spring({
    frame: Math.max(0, frame - 30),
    fps,
    config: { damping: 9, stiffness: 180, mass: 1 },
  });
  const checkX = interpolate(checkSpring, [0, 1], [-450, 0]);

  const xSpring = spring({
    frame: Math.max(0, frame - 34),
    fps,
    config: { damping: 9, stiffness: 180, mass: 1 },
  });
  const xX = interpolate(xSpring, [0, 1], [450, 0]);

  // Idle wobble for buttons
  const wobble = Math.sin(frame * 0.18) * 4;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {/* Green CHECK — left mid-frame */}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: "55%",
          transform: `translate(${checkX}px, -50%) rotate(${wobble * 0.6}deg)`,
          width: 220,
          height: 220,
          borderRadius: "50%",
          background: "#58CC02",
          boxShadow:
            "0 8px 0 #3D8A00, 0 14px 30px rgba(0,0,0,0.35)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 160,
          fontWeight: 900,
          color: "white",
          fontFamily,
          lineHeight: 1,
        }}
      >
        ✓
      </div>

      {/* Red X — right mid-frame */}
      <div
        style={{
          position: "absolute",
          right: 80,
          top: "55%",
          transform: `translate(${xX}px, -50%) rotate(${-wobble * 0.6}deg)`,
          width: 220,
          height: 220,
          borderRadius: "50%",
          background: "#EF4444",
          boxShadow:
            "0 8px 0 #991B1B, 0 14px 30px rgba(0,0,0,0.35)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 150,
          fontWeight: 900,
          color: "white",
          fontFamily,
          lineHeight: 1,
        }}
      >
        ✕
      </div>

      {/* Progress bar — visible 4-10s in body window (1s in, 1s before end).
          Inner Sequence from=30 dur=180 inside FactsOverlay's 240-frame window. */}
      <Sequence from={30} durationInFrames={180}>
        <ProgressBar />
      </Sequence>
    </AbsoluteFill>
  );
};

interface CellRect {
  left: string;
  top: string;
  width: string;
  height: string;
}

// Cell rects matching the body video's 2×2 / split / 3-up layout.
// Order matches the option order in the prompt: 4-opt → TL,TR,BL,BR;
// 3-opt → TL,TR,bottom-full; 2-opt → top,bottom.
function getGridCells(count: number): CellRect[] {
  if (count === 4) {
    return [
      { left: "0%",  top: "0%",  width: "50%",  height: "50%" }, // TL
      { left: "50%", top: "0%",  width: "50%",  height: "50%" }, // TR
      { left: "0%",  top: "50%", width: "50%",  height: "50%" }, // BL
      { left: "50%", top: "50%", width: "50%",  height: "50%" }, // BR
    ];
  }
  if (count === 3) {
    return [
      { left: "0%",  top: "0%",  width: "50%",  height: "50%" },
      { left: "50%", top: "0%",  width: "50%",  height: "50%" },
      { left: "0%",  top: "50%", width: "100%", height: "50%" },
    ];
  }
  if (count === 2) {
    return [
      { left: "0%", top: "0%",  width: "100%", height: "50%" },
      { left: "0%", top: "50%", width: "100%", height: "50%" },
    ];
  }
  // Fallback for unusual counts: stacked full-width rows.
  const rowH = `${100 / Math.max(1, count)}%`;
  return Array.from({ length: count }, (_, i) => ({
    left: "0%",
    top: `${(100 / count) * i}%`,
    width: "100%",
    height: rowH,
  }));
}

const ChoicesOverlay: React.FC<{
  options: ChoicesOption[];
  // Seconds to subtract from each revealAtSec — needed because this component
  // is mounted inside a <Sequence from={3s}> so useCurrentFrame() is
  // sequence-relative, while revealAtSec values are absolute video time.
  offsetSec?: number;
}> = ({ options, offsetSec = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cells = getGridCells(options.length);
  const fontSize = options.length === 2 ? 78 : 56;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {options.map((opt, i) => {
        const cell = cells[i] ?? cells[cells.length - 1];
        const revealFrame = Math.round((opt.revealAtSec - offsetSec) * fps);
        const sinceReveal = Math.max(0, frame - revealFrame);
        if (frame < revealFrame) return null;

        const entrance = spring({
          frame: sinceReveal,
          fps,
          config: { damping: 13, stiffness: 200, mass: 0.8 },
        });
        const opacity = interpolate(entrance, [0, 1], [0, 1]);
        const translateY = interpolate(entrance, [0, 1], [24, 0]);
        const scale = interpolate(entrance, [0, 1], [0.92, 1]);

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: cell.left,
              top: cell.top,
              width: cell.width,
              height: cell.height,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 28,
              boxSizing: "border-box",
            }}
          >
            <div
              style={{
                opacity,
                transform: `translateY(${translateY}px) scale(${scale})`,
                background: "rgba(10, 6, 30, 0.82)",
                borderRadius: 20,
                padding: "20px 28px",
                border: "3px solid rgba(255, 255, 255, 0.22)",
                boxShadow:
                  "0 12px 28px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.05) inset",
                fontFamily,
                fontWeight: 900,
                fontSize,
                color: "#FFFFFF",
                letterSpacing: -0.5,
                textTransform: "uppercase",
                lineHeight: 1.05,
                textShadow: "0 3px 0 #0a061e, 0 0 12px rgba(0,0,0,0.6)",
                textAlign: "center",
                maxWidth: "100%",
              }}
            >
              {opt.label}
            </div>
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

interface CaptionPage {
  words: WordCaption[];
  startMs: number;
  endMs: number;
}

// Group words into pages based on natural pauses (>=600ms) or max 3 words.
// Rebalance trailing 1-word orphans by pulling the last word forward.
//
// `forceBreakBeforeMs`: optional timeline mark. When a word's startMs is at
// or past this value AND the previous word's startMs was below it, a page
// break is forced before that word — regardless of MAX_WORDS / PAUSE_MS.
// Used to split the resolution caption from the CTA when there's no audio
// pause between them; the caller (component below) computes this mark by
// matching the canonical CTA text against the transcript.
function buildPages(
  words: WordCaption[],
  forceBreakBeforeMs?: number | null,
): CaptionPage[] {
  const pages: WordCaption[][] = [];
  let current: WordCaption[] = [];
  const MAX_WORDS = 3;
  const PAUSE_MS = 350;

  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const prev = words[i - 1];
    const gap = prev ? w.startMs - prev.endMs : 0;
    const crossesForcedBoundary =
      forceBreakBeforeMs != null &&
      prev != null &&
      prev.startMs < forceBreakBeforeMs &&
      w.startMs >= forceBreakBeforeMs;
    if (
      current.length > 0 &&
      (current.length >= MAX_WORDS ||
        gap >= PAUSE_MS ||
        crossesForcedBoundary)
    ) {
      pages.push(current);
      current = [];
    }
    current.push(w);
  }
  if (current.length) pages.push(current);

  // Avoid 1-word orphan: pull a word forward from the previous page.
  // Skip the rebalance when pulling would re-merge across the forced
  // boundary — better to keep a 1-word page than to undo the section split.
  // Also skip when the inter-word gap is wider than SECTION_GAP_MS: a multi-
  // second silence is a section break (e.g. body→closer xfade), and combining
  // those pages would make the closer's caption visible for seconds before
  // its audio actually starts. Better to leave the orphan alone.
  const SECTION_GAP_MS = 1500;
  for (let i = pages.length - 1; i > 0; i--) {
    if (pages[i].length === 1 && pages[i - 1].length > 1) {
      const candidate = pages[i - 1][pages[i - 1].length - 1];
      const wouldUnmergeForcedBreak =
        forceBreakBeforeMs != null &&
        candidate.startMs < forceBreakBeforeMs &&
        pages[i][0].startMs >= forceBreakBeforeMs;
      if (wouldUnmergeForcedBreak) continue;
      const gapAcrossPull = pages[i][0].startMs - candidate.endMs;
      if (gapAcrossPull >= SECTION_GAP_MS) continue;
      const moved = pages[i - 1].pop()!;
      pages[i].unshift(moved);
    }
  }

  return pages.map((pageWords) => ({
    words: pageWords,
    startMs: pageWords[0].startMs,
    endMs: pageWords[pageWords.length - 1].endMs,
  }));
}


// Find the actual startMs of the CTA in the transcript. We search for the
// first word whose normalized text matches `ctaText`'s first word, but only
// past `nominalStartMs - WHISPER_DRIFT_MS` so we don't latch onto an earlier
// accidental occurrence (e.g. the resolution happens to mention "first" too).
const WHISPER_DRIFT_MS = 1500;
function findCtaStartMs(
  words: WordCaption[],
  ctaText: string | null | undefined,
  nominalStartMs: number | null | undefined,
): number | null {
  if (!ctaText || nominalStartMs == null) return null;
  const norm = (s: string) =>
    s.toLowerCase().replace(/[^a-z0-9]+/g, "").trim();
  const target = norm(ctaText.split(/\s+/)[0] || "");
  if (!target) return null;
  const minStartMs = nominalStartMs - WHISPER_DRIFT_MS;
  for (const w of words) {
    if (w.startMs < minStartMs) continue;
    if (norm(w.word) === target) return w.startMs;
  }
  return null;
}

const TikTokPage: React.FC<{
  page: CaptionPage;
  highlightColor: string;
  baseColor: string;
  fontSize: number;
}> = ({ page, highlightColor, baseColor, fontSize }) => {
  // useCurrentFrame inside <Sequence> is already sequence-relative
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentMs = page.startMs + (frame / fps) * 1000;

  const entrance = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 180, mass: 0.7 },
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        // Very bottom — below the poll buttons in bg video
        paddingBottom: 180,
      }}
    >
      <div
        style={{
          opacity: entrance,
          transform: `translateY(${interpolate(entrance, [0, 1], [20, 0])}px)`,
          fontFamily,
          fontWeight: 900,
          fontSize,
          lineHeight: 1.15,
          textAlign: "center",
          textTransform: "uppercase",
          letterSpacing: -0.5,
          maxWidth: "86%",
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          alignItems: "center",
          columnGap: 18,
          rowGap: 14,
        }}
      >
        {page.words.map((w, i) => {
          const isActive = w.startMs <= currentMs && currentMs < w.endMs + 80;
          const activationFrame = ((w.startMs - page.startMs) / 1000) * fps;
          const popFrame = Math.max(0, frame - activationFrame);
          const pop = isActive
            ? spring({
                frame: popFrame,
                fps,
                config: { damping: 11, stiffness: 260, mass: 0.5 },
              })
            : 0;
          const scale = isActive ? 1 + pop * 0.06 : 1;
          return (
            <span
              key={`${w.startMs}-${i}`}
              style={{
                display: "inline-block",
                transform: `scale(${scale})`,
                transformOrigin: "center",
                color: isActive ? "#0a061e" : baseColor,
                backgroundColor: isActive ? highlightColor : "transparent",
                padding: "6px 22px 10px",
                borderRadius: 18,
                WebkitTextStroke: isActive ? "0" : "2.5px #0a061e",
                paintOrder: "stroke fill",
                textShadow: isActive
                  ? "none"
                  : "0 4px 0 #0a061e, 0 0 14px rgba(0,0,0,0.6)",
              }}
            >
              {w.word.replace(/[.,]$/g, "")}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const TriviaWithBg: React.FC<TriviaWithBgProps> = ({
  videoSrc,
  words,
  darkOverlay = 0,
  highlightColor = "#22E88A",
  baseColor = "#FFFFFF",
  fontSize = 78,
  showFactsOverlay = true,
  mode = "Facts",
  options = [],
  suppressCaptionsWindowMs = null,
  ctaText = null,
  ctaNominalStartMs = null,
}) => {
  const { fps, durationInFrames } = useVideoConfig();
  const isChoices = mode === "Choices";

  // Filter individual words inside the suppression window BEFORE page-building.
  // (Filtering whole pages after buildPages can drop adjacent words because
  // orphan-rebalancing may pull a pre-window word into a post-window page,
  // then the merged page is dropped wholesale.)
  //
  // Asymmetric rule, by audio endMs:
  //   - end inside (s,e)   → drop. Option-letter prefix or option name.
  //                         Its audio finishes while the option-reveal
  //                         animation is still running.
  //   - end past e         → keep. Resolution / CTA word whose audio
  //                         carries past the suppression boundary. The
  //                         old symmetric overlap test dropped these,
  //                         deleting the first word of the resolution VO
  //                         (e.g. "Floating" in "Floating duo") and
  //                         leaving buildPages to assemble a wrong page
  //                         like [duo, Lock, yours] instead of
  //                         [Floating, duo, Lock].
  //   - end before s       → keep. Pre-option content (hook tail, claim).
  //
  // Then for any kept word whose audio START is inside the window, clamp
  // its display startMs to the window end. That keeps its caption page
  // hidden until suppression lifts, instead of flashing on screen during
  // the last beat of the option-reveal animation.
  let visibleWords: WordCaption[] = words;
  if (suppressCaptionsWindowMs) {
    const [s, e] = suppressCaptionsWindowMs;
    visibleWords = words
      .filter((w) => !(w.endMs > s && w.endMs < e))
      .map((w) =>
        w.startMs > s && w.startMs < e ? { ...w, startMs: e } : w,
      );
  }
  // Resolve the CTA-start position from the post-filter transcript. We
  // compute it AFTER the suppress-window clamp so any startMs nudges have
  // already been applied — the boundary lives in the same timeline the
  // page-builder will see.
  const ctaStartMs = findCtaStartMs(visibleWords, ctaText, ctaNominalStartMs);
  const pages = buildPages(visibleWords, ctaStartMs);

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {/* Background video — cover fit */}
      <AbsoluteFill>
        <OffthreadVideo
          src={videoSrc}
          muted={false}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      </AbsoluteFill>

      {/* Dark overlay for text readability */}
      {darkOverlay > 0 && (
        <AbsoluteFill
          style={{
            backgroundColor: `rgba(0, 0, 0, ${darkOverlay})`,
          }}
        />
      )}

      {/* Mechanic overlay during body window (3-11s) — Facts: check/X + progress;
          Choices: stacked option reveal. */}
      {isChoices ? (
        options.length > 0 && (
          // 7.4s — body window ends at 10.4s where the body→closer xfade starts.
          // Options must clear before the closer so the avatar isn't covered.
          <Sequence
            from={Math.round(3 * fps)}
            durationInFrames={Math.round(7.4 * fps)}
          >
            <ChoicesOverlay options={options} offsetSec={3} />
          </Sequence>
        )
      ) : (
        showFactsOverlay && (
          <Sequence
            from={Math.round(3 * fps)}
            durationInFrames={Math.round(8 * fps)}
          >
            <FactsOverlay />
          </Sequence>
        )
      )}

      {/* TikTok-style word-highlighted captions */}
      {pages.map((page, i) => {
        const fromFrame = Math.max(0, Math.round((page.startMs / 1000) * fps));
        // End shortly after last word finishes, but NEVER overlap the next page.
        const nextStartMs = pages[i + 1]?.startMs ?? Infinity;
        const endMs = Math.min(page.endMs + 400, nextStartMs);
        const endFrame = Math.min(
          durationInFrames,
          Math.round((endMs / 1000) * fps),
        );
        const duration = Math.max(1, endFrame - fromFrame);
        return (
          <Sequence key={i} from={fromFrame} durationInFrames={duration}>
            <TikTokPage
              page={page}
              highlightColor={highlightColor}
              baseColor={baseColor}
              fontSize={fontSize}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

// Ignore staticFile import if unused — kept for potential future asset ref
void staticFile;
