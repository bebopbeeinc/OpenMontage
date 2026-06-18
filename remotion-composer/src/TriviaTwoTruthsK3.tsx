import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as ldMont } from "@remotion/google-fonts/Montserrat";
import { loadFont as ldAnton } from "@remotion/google-fonts/Anton";
import { loadFont as ldFredoka } from "@remotion/google-fonts/Fredoka";
import type { WordCaption } from "./components/CaptionOverlay";
import { buildPages, TikTokPage } from "./TriviaWithBg";

const MONT = ldMont("normal", { weights: ["900"] }).fontFamily;
const ANTON = ldAnton().fontFamily;
const FREDOKA = ldFredoka("normal", { weights: ["600", "700"] }).fontFamily;

// VERSION K3 — themed. Default (minimal) layout = one centered header lockup:
// the TC logo + "📍 place" pill (the "2 TRUTHS, 1 LIE" title was removed), kept
// inside the TikTok safe zone (below the top tabs, clear of the bottom caption
// strip and right action rail). Set minimal=false to restore the legacy
// bottom-stacking fact banners. Pick palette/font via `themeName`.
interface Theme {
  font: string;
  italic: boolean;
  barBg: string;
  borderGrad: string; // CSS gradient for the bar border
  text: string;
  numBg: string;
  numText: string;
  emphasisColor: string; // for the LIE word
  emphasisGrad?: string;
  glow: string;
  scrim: string;
}

const THEMES: Record<string, Theme> = {
  neon: {
    font: MONT,
    italic: true,
    barBg: "#0A0A1E",
    borderGrad: "linear-gradient(95deg, #00E5FF, #FF2FB0)",
    text: "#FFFFFF",
    numBg: "linear-gradient(135deg, #00E5FF, #FF2FB0)",
    numText: "#0A0A1E",
    emphasisColor: "#00E5FF",
    emphasisGrad: "linear-gradient(95deg, #00E5FF, #FF2FB0)",
    glow: "0 0 22px rgba(0,229,255,0.4), 0 0 34px rgba(255,47,176,0.3)",
    scrim: "rgba(0,0,12,0.5)",
  },
  gold: {
    font: ANTON,
    italic: false,
    barBg: "linear-gradient(180deg, #122a5e, #0c1c40)",
    borderGrad: "linear-gradient(95deg, #FFE08A, #FF9A2E)",
    text: "#FFF6E0",
    numBg: "linear-gradient(135deg, #FFE08A, #FF9A2E)",
    numText: "#3a1d00",
    emphasisColor: "#FFC23C",
    emphasisGrad: "linear-gradient(95deg, #FFE08A, #FF9A2E)",
    glow: "0 0 20px rgba(255,180,60,0.45)",
    scrim: "rgba(4,8,24,0.55)",
  },
  goldround: {
    font: FREDOKA,
    italic: false,
    barBg: "linear-gradient(180deg, #122a5e, #0c1c40)",
    borderGrad: "linear-gradient(95deg, #FFE08A, #FF9A2E)",
    text: "#FFF6E0",
    numBg: "linear-gradient(135deg, #FFE08A, #FF9A2E)",
    numText: "#3a1d00",
    emphasisColor: "#FFC23C",
    emphasisGrad: "linear-gradient(95deg, #FFE08A, #FF9A2E)",
    glow: "0 0 20px rgba(255,180,60,0.45)",
    scrim: "rgba(4,8,24,0.55)",
  },
  candy: {
    font: FREDOKA,
    italic: false,
    barBg: "linear-gradient(180deg, #FFFFFF, #EAF1FB)",
    borderGrad: "linear-gradient(95deg, #FF6F61, #FFB24A)",
    text: "#13245C",
    numBg: "linear-gradient(135deg, #FF6F61, #E23B28)",
    numText: "#FFFFFF",
    emphasisColor: "#FF5A47",
    glow: "0 0 18px rgba(255,111,97,0.4)",
    scrim: "rgba(0,0,18,0.4)",
  },
};

export interface TriviaTwoTruthsK3Props {
  videoSrc: string;
  logoSrc: string;
  claims: { label: string; revealAtSec: number }[];
  title?: string;
  place?: string;
  themeName?: keyof typeof THEMES;
  // Minimal layout (default): only the centered header lockup (title + place),
  // no bottom fact-reveal banners. The claims are spoken-only. This keeps every
  // graphic inside the TikTok safe zone — clear of the top tabs, the bottom
  // caption strip, and the right action rail (the v1 full-bleed bars were
  // getting clipped by all three). Set false to restore the old K3 fact bars.
  minimal?: boolean;
  // Word-level karaoke captions burned at the bottom, identical to the
  // ellie.travelcrush (trivia-reaction) style — reuses buildPages + TikTokPage.
  words?: WordCaption[];
  highlightColor?: string;
  baseColor?: string;
  fontSize?: number;
}

const BAR_H = 120;
const GAP = 16;
const PAD_BOTTOM = 50;
const BLEED = 10; // push past screen edges so side borders never show

// TikTok safe zone (1080×1920). The header lockup lives below SAFE_TOP so the
// "For You / Following" tabs never cover it; auto-width + centered keeps it out
// of the right action rail.
const SAFE_TOP = 250;

const barShell = (t: Theme): React.CSSProperties => ({
  border: "5px solid transparent",
  background: `linear-gradient(${t.barBg.includes("gradient") ? "" : ""}) `, // placeholder, overridden below
});

const shell = (t: Theme): React.CSSProperties => ({
  // edge-to-edge band: gradient border via two-layer background; side borders bleed off-screen
  border: "5px solid transparent",
  background: `${t.barBg} padding-box, ${t.borderGrad} border-box`,
  boxShadow: `${t.glow}, 0 12px 28px rgba(0,0,0,0.5)`,
});

// Header lockup = the TC logo + "📍 place" pill, centered in the top safe band.
// The "2 TRUTHS, 1 LIE" title was removed — brand mark + location is the whole
// persistent overlay now.
const PlaceBanner: React.FC<{ place: string; logoSrc: string; t: Theme }> = ({
  place,
  logoSrc,
  t,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const e = spring({ frame, fps, config: { damping: 14, stiffness: 180 } });
  const y = interpolate(e, [0, 1], [-120, 0]);
  const op = interpolate(e, [0, 1], [0, 1]);
  return (
    <div
      style={{
        position: "absolute",
        top: SAFE_TOP,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        transform: `translateY(${y}px)`,
        opacity: op,
      }}
    >
      <div
        style={{
          padding: "14px 34px",
          borderRadius: 44,
          display: "inline-flex",
          alignItems: "center",
          gap: 22,
          maxWidth: 960,
          ...shell(t),
        }}
      >
        <Img src={logoSrc} style={{ height: 72 }} />
        <span
          style={{
            fontFamily: t.font,
            fontWeight: 900,
            fontStyle: t.italic ? "italic" : "normal",
            fontSize: 48,
            color: t.text,
            textTransform: "uppercase",
            letterSpacing: t.font === ANTON ? 0 : -0.5,
            textShadow: "0 2px 8px rgba(0,0,0,0.5)",
          }}
        >
          📍 {place}
        </span>
      </div>
    </div>
  );
};

const FactBar: React.FC<{
  n: number;
  label: string;
  reveals: number[];
  index: number;
  t: Theme;
}> = ({ n, label, reveals, index, t }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sp = (f: number) =>
    spring({ frame: frame - f, fps, config: { damping: 15, stiffness: 170, mass: 0.9 } });

  const myReveal = reveals[index];
  if (frame < myReveal) return null;
  const e = sp(myReveal);
  const enterY = interpolate(e, [0, 1], [100, 0]);
  const enterScale = interpolate(e, [0, 1], [0.86, 1]);
  const op = interpolate(e, [0, 1], [0, 1]);
  const flash = interpolate(frame - myReveal, [0, 5, 16], [0, 0.7, 0], { extrapolateRight: "clamp" });

  let rise = 0;
  for (let j = index + 1; j < reveals.length; j++) rise += (BAR_H + GAP) * sp(reveals[j]);

  return (
    <div
      style={{
        position: "absolute",
        left: -BLEED,
        right: -BLEED,
        bottom: PAD_BOTTOM + rise,
        height: BAR_H,
        transform: `translateY(${enterY}px) scale(${enterScale})`,
        transformOrigin: "center bottom",
        opacity: op,
        display: "flex",
        alignItems: "center",
        gap: 24,
        padding: `0 36px 0 ${24 + BLEED}px`,
        overflow: "hidden",
        ...shell(t),
      }}
    >
      {flash > 0 && (
        <div style={{ position: "absolute", inset: 0, background: "#fff", opacity: flash }} />
      )}
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: 16,
          background: t.numBg,
          color: t.numText,
          fontFamily: t.font,
          fontWeight: 900,
          fontStyle: t.italic ? "italic" : "normal",
          fontSize: 50,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flex: "0 0 auto",
        }}
      >
        {n}
      </div>
      <span
        style={{
          fontFamily: t.font,
          fontWeight: 900,
          fontStyle: t.italic ? "italic" : "normal",
          fontSize: 56,
          color: t.text,
          textTransform: "uppercase",
          letterSpacing: t.font === ANTON ? 0 : -0.5,
          textShadow:
            t.text === "#FFFFFF" || t.text === "#FFF6E0"
              ? "0 2px 8px rgba(0,0,0,0.55)"
              : "none",
        }}
      >
        {label}
      </span>
    </div>
  );
};

export const TriviaTwoTruthsK3: React.FC<TriviaTwoTruthsK3Props> = ({
  videoSrc,
  logoSrc,
  claims,
  place = "The Bahamas",
  themeName = "neon",
  minimal = true,
  words = [],
  highlightColor = "#D63B2F", // 2t1l brand warm-red pill (styles/trivia-captain-2t1l.yaml)
  baseColor = "#FFFFFF",
  fontSize = 78,
}) => {
  const { fps, durationInFrames } = useVideoConfig();
  const t = THEMES[themeName] ?? THEMES.neon;
  const reveals = claims.map((c) => c.revealAtSec * fps);
  const pages = buildPages(words);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <OffthreadVideo src={videoSrc} muted={false} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, ${t.scrim} 0%, rgba(0,0,12,0) 18%, rgba(0,0,12,0) 66%, ${t.scrim} 100%)`,
          pointerEvents: "none",
        }}
      />
      <PlaceBanner place={place} logoSrc={logoSrc} t={t} />
      {!minimal &&
        claims.map((c, i) => (
          <FactBar key={i} n={i + 1} label={c.label} reveals={reveals} index={i} t={t} />
        ))}
      {/* Word-level karaoke captions — same renderer as ellie.travelcrush. */}
      {pages.map((page, i) => {
        const fromFrame = Math.max(0, Math.round((page.startMs / 1000) * fps));
        const nextStartMs = pages[i + 1]?.startMs ?? Infinity;
        const endMs = Math.min(page.endMs + 400, nextStartMs);
        const endFrame = Math.min(durationInFrames, Math.round((endMs / 1000) * fps));
        const duration = Math.max(1, endFrame - fromFrame);
        return (
          <Sequence key={i} from={fromFrame} durationInFrames={duration}>
            <TikTokPage page={page} highlightColor={highlightColor} baseColor={baseColor} fontSize={fontSize} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

void barShell;
