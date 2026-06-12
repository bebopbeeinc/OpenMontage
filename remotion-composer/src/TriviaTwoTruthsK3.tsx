import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as ldMont } from "@remotion/google-fonts/Montserrat";
import { loadFont as ldAnton } from "@remotion/google-fonts/Anton";
import { loadFont as ldFredoka } from "@remotion/google-fonts/Fredoka";

const MONT = ldMont("normal", { weights: ["900"] }).fontFamily;
const ANTON = ldAnton().fontFamily;
const FREDOKA = ldFredoka("normal", { weights: ["600", "700"] }).fontFamily;

// VERSION K3 — themed. Full-bleed (edge-to-edge) top title banner +
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
}

const BAR_H = 120;
const GAP = 16;
const PAD_BOTTOM = 50;
const BLEED = 10; // push past screen edges so side borders never show

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

const TopTitle: React.FC<{ title: string; logoSrc: string; t: Theme }> = ({
  title,
  logoSrc,
  t,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const e = spring({ frame, fps, config: { damping: 14, stiffness: 180 } });
  const y = interpolate(e, [0, 1], [-180, 0]);
  const parts = title.split(/(\bLIE\b)/i);
  return (
    <div
      style={{
        position: "absolute",
        top: 48,
        left: -BLEED,
        right: -BLEED,
        height: 156,
        transform: `translateY(${y}px)`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 26,
        ...shell(t),
      }}
    >
      <span
        style={{
          fontFamily: t.font,
          fontWeight: 900,
          fontStyle: t.italic ? "italic" : "normal",
          fontSize: 78,
          color: t.text,
          textTransform: "uppercase",
          letterSpacing: t.font === ANTON ? 0 : -1,
          textShadow: "0 3px 10px rgba(0,0,0,0.55)",
        }}
      >
        {parts.map((p, i) =>
          /^lie$/i.test(p) ? (
            <span
              key={i}
              style={{
                display: "inline-block",
                padding: "0 0.1em",
                lineHeight: 1.3,
                ...(t.emphasisGrad
                  ? {
                      background: t.emphasisGrad,
                      WebkitBackgroundClip: "text",
                      WebkitTextFillColor: "transparent",
                    }
                  : { color: t.emphasisColor }),
              }}
            >
              {p}
            </span>
          ) : (
            <span key={i}>{p}</span>
          ),
        )}
      </span>
      <Img src={logoSrc} style={{ height: 90 }} />
    </div>
  );
};

const PlaceBanner: React.FC<{ place: string; t: Theme }> = ({ place, t }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const e = spring({ frame: frame - 6, fps, config: { damping: 14, stiffness: 200 } });
  const y = interpolate(e, [0, 1], [-44, 0]);
  const op = interpolate(e, [0, 1], [0, 1]);
  return (
    <div
      style={{
        position: "absolute",
        top: 224,
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
          padding: "12px 38px",
          borderRadius: 44,
          display: "flex",
          alignItems: "center",
          gap: 12,
          ...shell(t),
        }}
      >
        <span
          style={{
            fontFamily: t.font,
            fontWeight: 900,
            fontStyle: t.italic ? "italic" : "normal",
            fontSize: 44,
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
  title = "2 TRUTHS, 1 LIE",
  place = "The Bahamas",
  themeName = "neon",
}) => {
  const { fps } = useVideoConfig();
  const t = THEMES[themeName] ?? THEMES.neon;
  const reveals = claims.map((c) => c.revealAtSec * fps);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <OffthreadVideo src={videoSrc} muted={false} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, ${t.scrim} 0%, rgba(0,0,12,0) 18%, rgba(0,0,12,0) 66%, ${t.scrim} 100%)`,
          pointerEvents: "none",
        }}
      />
      <TopTitle title={title} logoSrc={logoSrc} t={t} />
      <PlaceBanner place={place} t={t} />
      {claims.map((c, i) => (
        <FactBar key={i} n={i + 1} label={c.label} reveals={reveals} index={i} t={t} />
      ))}
    </AbsoluteFill>
  );
};

void barShell;
