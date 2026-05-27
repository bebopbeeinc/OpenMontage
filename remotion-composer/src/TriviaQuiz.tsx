import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as loadSans } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadSerif } from "@remotion/google-fonts/Fraunces";

// Two-font system gives the show typographic personality:
// - Fraunces (modern editorial serif, italic) for the QUESTION text — the
//   hero copy per beat. Reads "magazine question of the day", not "quiz app".
// - Montserrat 900 (geometric sans, heavy) for utility: labels, choices,
//   stamps, the locked hook. Clean and scannable.
const { fontFamily: sansFamily }  = loadSans("normal",  { weights: ["700", "900"] });
const { fontFamily: serifFamily } = loadSerif("italic", { weights: ["600", "900"] });
const fontFamily = sansFamily;  // legacy alias used throughout the file

// ---------------------------------------------------------------------------
// Types — match scripts/trivia_quiz/assemble_quiz.py's quiz_meta.json output
// ---------------------------------------------------------------------------

export interface QuizQuestion {
  id: "q1" | "q2" | "q3";
  start_s: number;             // absolute timeline position
  duration_s: number;          // segment length
  question: string;
  choices: string[];           // ["A) ...", "B) ...", "C) ..."] (empty for T/F)
  answer_index: number;        // 0-based index into choices (for T/F: 0=true, 1=false)
  answer_label: string;        // spoken label, e.g. "Unicorn"
  countdown_start_s: number;   // relative to segment
  countdown_duration_s: number;
  reveal_at_s: number;         // relative to segment (when stamp lands)
  surprise_fact: string;
  difficulty: "Easy" | "Medium" | "Hard";
}

export interface QuizMeta {
  show: {
    title: string;
    hook: string;
    closer: {
      intro: string;
      emphasis: string;
      cta: string;
    };
    lockup_text: string;
    lockup_brand: string;
    placeholder_url: string;
  };
  questions: QuizQuestion[];
  score_card: {
    start_s: number;
    bottom_cta: string;
    reward: string;
    game_hook_line: string;
  };
}

export interface TriviaQuizProps {
  videoSrc: string;            // bg.mp4 (solid color or stills+KenBurns + audio)
  meta: QuizMeta;
}

// ---------------------------------------------------------------------------
// Palette — locked in styles/trivia-quiz.yaml
// ---------------------------------------------------------------------------

const COLORS = {
  // Travel-coded "evening sky" gradient — readable, brand-aligned, distinctive
  // versus the flat-navy "fintech" look. Used by BrandBackdrop below.
  gradientTop:      "#0A1228",   // deep night blue (top of sky)
  gradientMid:      "#1E1B4B",   // indigo (twilight band)
  gradientBottom:   "#3D1E5E",   // dusk purple (horizon)

  hookText:         "#FBBF24",   // warm yellow, hook hero + stroke
  questionBg:       "rgba(10, 18, 40, 0.55)",
  questionText:     "#FFFFFF",
  questionSerif:    "#FEF3C7",   // creamy off-white for serif question — softer than pure #fff
  countdownSafe:    "#58CC02",
  countdownWarn:    "#FBBF24",
  countdownDanger:  "#EF4444",
  revealCorrect:    "#58CC02",
  revealStroke:     "#0A061E",
  surpriseFact:     "#FBBF24",
  brandOrange:      "#FF6A2C",   // stamp + lockup brand color
  globeWire:        "rgba(251, 191, 36, 0.04)",  // barely-there placeholder — OpenArt replaces in v0.2
  // Per-difficulty stamp colors
  easyStamp:        "#58CC02",
  mediumStamp:      "#FBBF24",
  hardStamp:        "#EF4444",
  // Letter-badge palette: A/B/C get distinct hues that feel game-show-y
  letterBadgeA:     "#FB7185",   // rose
  letterBadgeB:     "#60A5FA",   // sky blue
  letterBadgeC:     "#A78BFA",   // violet
  letterBadgeBg:    "rgba(255, 255, 255, 0.08)",
};

// ---------------------------------------------------------------------------
// TextCard — translucent dark backing behind text blocks. Ensures contrast
// holds when the v0.2 OpenArt clip plays behind the UI. All text overlays
// (hook hero, question, surprise fact, score prompt, tomorrow tease) sit
// inside one of these.
// ---------------------------------------------------------------------------

const TextCard: React.FC<{
  children: React.ReactNode;
  maxWidth?: number;
  padding?: string;
  variant?: "default" | "subtle" | "stamp";
}> = ({ children, maxWidth, padding = "26px 44px", variant = "default" }) => {
  const bg =
    variant === "stamp"   ? "rgba(10, 18, 40, 0.88)" :
    variant === "subtle"  ? "rgba(10, 18, 40, 0.62)" :
    "rgba(10, 18, 40, 0.78)";
  return (
    <div style={{
      backgroundColor: bg,
      borderRadius: 32,
      padding,
      maxWidth: maxWidth ?? "100%",
      backdropFilter: "blur(8px)",
      WebkitBackdropFilter: "blur(8px)",
      border: "1px solid rgba(255, 255, 255, 0.08)",
      boxShadow: "0 10px 32px rgba(0, 0, 0, 0.45)",
      display: "inline-block",
    }}>
      {children}
    </div>
  );
};

// ---------------------------------------------------------------------------
// BrandBackdrop — gradient sky + faint globe wireframe behind every screen
// ---------------------------------------------------------------------------

const BrandBackdrop: React.FC<{ tint?: string }> = ({ tint }) => {
  const frame = useCurrentFrame();
  // Slow, almost imperceptible drift so the gradient doesn't feel static.
  const drift = (frame * 0.04) % 360;
  return (
    <AbsoluteFill style={{
      background: `linear-gradient(${180 + Math.sin(drift * Math.PI / 180) * 4}deg, ${COLORS.gradientTop} 0%, ${COLORS.gradientMid} 55%, ${COLORS.gradientBottom} 100%)`,
    }}>
      {/* Globe wireframe overlay — atmospheric, never the focus */}
      <svg
        width="1080" height="1920"
        viewBox="0 0 1080 1920"
        style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
      >
        <defs>
          <clipPath id="globeClip">
            <circle cx="540" cy="960" r="900" />
          </clipPath>
        </defs>
        <g stroke={COLORS.globeWire} strokeWidth="2" fill="none" clipPath="url(#globeClip)">
          {/* Latitude lines (ellipses) */}
          {[0.2, 0.4, 0.6, 0.8].map((y, i) => (
            <ellipse key={`lat-${i}`} cx="540" cy="960" rx="900" ry={900 * y} />
          ))}
          {/* Longitude lines (rotated ellipses suggesting a sphere) */}
          {[0, 30, 60, 90, 120, 150].map((deg) => (
            <ellipse
              key={`lng-${deg}`}
              cx="540" cy="960" rx="900" ry="280"
              transform={`rotate(${deg} 540 960)`}
            />
          ))}
          {/* Equator emphasized */}
          <line x1="-200" y1="960" x2="1280" y2="960" strokeWidth="3" opacity="1.5" />
        </g>
      </svg>
      {tint && <AbsoluteFill style={{ backgroundColor: tint, mixBlendMode: "multiply" }} />}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// DifficultyStamp — angled passport-stamp badge ("EASY" / "MEDIUM" / "HARD")
// Replaces the plain "Q1 • EASY" text line at the top of each question.
// ---------------------------------------------------------------------------

const DifficultyStamp: React.FC<{ qid: string; difficulty: "Easy" | "Medium" | "Hard" }> = ({ qid, difficulty }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, durationInFrames: 14, config: { damping: 9, stiffness: 220 } });
  const rotation = interpolate(enter, [0, 1], [-32, -6]);
  const scale = interpolate(enter, [0, 1], [1.5, 1]);

  const stampColor =
    difficulty === "Easy"   ? COLORS.easyStamp :
    difficulty === "Medium" ? COLORS.mediumStamp :
    COLORS.hardStamp;

  return (
    <div style={{
      display: "inline-block",
      transform: `rotate(${rotation}deg) scale(${scale})`,
      opacity: enter,
      padding: "16px 40px",
      border: `5px solid ${stampColor}`,
      borderRadius: 14,
      // Dark scrim fill so the stamp's colored text + ROUND label stay
      // readable against any OpenArt backdrop. Without this, ROUND X OF 3
      // (the smaller line) loses contrast on busy images.
      backgroundColor: "rgba(10, 18, 40, 0.86)",
      backdropFilter: "blur(4px)",
      boxShadow: `0 6px 18px rgba(0, 0, 0, 0.4), inset 0 0 0 2px ${stampColor}22`,
      position: "relative",
    }}>
      <div style={{
        fontFamily: sansFamily,
        fontWeight: 900,
        fontSize: 56,
        color: stampColor,
        letterSpacing: 6,
        lineHeight: 1,
        textTransform: "uppercase",
        textShadow: "0 2px 6px rgba(0, 0, 0, 0.5)",
      }}>
        {difficulty}
      </div>
      <div style={{
        fontFamily: sansFamily,
        fontWeight: 800,
        fontSize: 22,
        color: stampColor,
        letterSpacing: 4,
        opacity: 0.95,
        textAlign: "center",
        marginTop: 8,
        textTransform: "uppercase",
      }}>
        ROUND {qid.replace("q", "")} of 3
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// LetterBadge — circular A/B/C badge with bold letter, used in choice rows
// ---------------------------------------------------------------------------

const LetterBadge: React.FC<{
  letter: string;
  color: string;
  showRevealStyle?: boolean;
}> = ({ letter, color, showRevealStyle }) => {
  return (
    <div style={{
      width: 110,
      height: 110,
      flexShrink: 0,
      borderRadius: "50%",
      backgroundColor: showRevealStyle ? "#FFFFFF" : color,
      border: showRevealStyle ? `5px solid ${COLORS.revealCorrect}` : `5px solid rgba(255,255,255,0.18)`,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: sansFamily,
      fontWeight: 900,
      fontSize: 56,
      color: showRevealStyle ? COLORS.revealCorrect : "#FFFFFF",
      boxShadow: showRevealStyle
        ? "0 6px 18px rgba(88, 204, 2, 0.45), inset 0 -4px 0 rgba(0,0,0,0.12)"
        : "0 6px 18px rgba(0,0,0,0.35), inset 0 -4px 0 rgba(0,0,0,0.18)",
      transition: "background-color 0.2s linear, color 0.2s linear",
    }}>
      {letter}
    </div>
  );
};

// ---------------------------------------------------------------------------
// PassportStamp — orange circular stamp that slams onto correct answer
// ---------------------------------------------------------------------------

const PassportStamp: React.FC<{ delayFrames?: number }> = ({ delayFrames = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const f = frame - delayFrames;
  if (f < 0) return null;

  const slam = spring({ frame: f, fps, durationInFrames: 12, config: { damping: 8, stiffness: 260, mass: 1 } });
  const scale = interpolate(slam, [0, 1], [1.8, 1]);
  const rotation = interpolate(slam, [0, 1], [-22, -8]);
  const opacity = interpolate(f, [0, 4, 1000], [0, 1, 1]);
  // Subtle wobble after slam
  const wobble = Math.sin(f * 0.15) * 0.6 * Math.exp(-f * 0.04);

  return (
    <div style={{
      position: "absolute",
      right: -10,
      top: -22,
      width: 160,
      height: 160,
      transform: `rotate(${rotation + wobble}deg) scale(${scale})`,
      opacity,
      filter: "drop-shadow(0 4px 12px rgba(255,106,44,0.35))",
    }}>
      <svg viewBox="0 0 160 160" width="160" height="160">
        {/* Outer ring */}
        <circle cx="80" cy="80" r="72" fill="none" stroke={COLORS.brandOrange} strokeWidth="6" />
        {/* Inner ring */}
        <circle cx="80" cy="80" r="58" fill="none" stroke={COLORS.brandOrange} strokeWidth="3" />
        {/* Checkmark */}
        <path d="M 50 82 L 72 104 L 116 60"
              fill="none" stroke={COLORS.brandOrange} strokeWidth="11" strokeLinecap="round" strokeLinejoin="round" />
        {/* Top label */}
        <text x="80" y="34" textAnchor="middle" fontFamily={fontFamily}
              fontSize="11" fontWeight="900" fill={COLORS.brandOrange} letterSpacing="2">
          CORRECT
        </text>
        {/* Bottom label */}
        <text x="80" y="138" textAnchor="middle" fontFamily={fontFamily}
              fontSize="10" fontWeight="700" fill={COLORS.brandOrange} letterSpacing="1.5">
          ✈ TRAVEL CRUSH
        </text>
      </svg>
    </div>
  );
};

// ---------------------------------------------------------------------------
// PlaneTransition — small plane flies left-to-right with a dotted contrail
// ---------------------------------------------------------------------------

const PlaneTransition: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  // Frame 0 → frame durationInFrames maps to x position from -200px to 1280px
  const x = interpolate(frame, [0, durationInFrames], [-200, 1280]);
  const y = interpolate(frame, [0, durationInFrames], [220, 180], { extrapolateRight: "clamp" });
  // Plane fades in/out at the edges
  const fade = interpolate(frame, [0, 6, durationInFrames - 6, durationInFrames], [0, 1, 1, 0], { extrapolateRight: "clamp" });

  // Build contrail trail (10 dots behind the plane, fading)
  const dots = Array.from({ length: 14 }, (_, i) => {
    const trailX = x - (i + 1) * 38;
    const trailY = y + (i * 1.2);
    return { x: trailX, y: trailY, opacity: (1 - i / 14) * 0.5 * fade };
  });

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {dots.map((d, i) => (
        <div key={i} style={{
          position: "absolute",
          left: d.x,
          top: d.y,
          width: 10,
          height: 10,
          borderRadius: "50%",
          backgroundColor: COLORS.hookText,
          opacity: d.opacity,
        }} />
      ))}
      <div style={{
        position: "absolute",
        left: x,
        top: y - 20,
        fontSize: 64,
        opacity: fade,
        transform: "scaleX(1)",
        filter: "drop-shadow(0 0 12px rgba(251,191,36,0.4))",
      }}>
        ✈️
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// HookCard — segment 0–3s
// ---------------------------------------------------------------------------

const HookCard: React.FC<{ title: string; hook: string; totalFrames: number }> = ({ title, hook, totalFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame, fps, durationInFrames: 14, config: { damping: 14 } });
  const subtitleEnter = spring({ frame: frame - 6, fps, durationInFrames: 16, config: { damping: 13 } });
  const hookEnter = spring({ frame: frame - 14, fps, durationInFrames: 18, config: { damping: 12 } });
  // Quick entry + exit fades (~8 frames each). HookCard's Sequence ends
  // exactly when Q1 starts — no time overlap — so only one card UI is on
  // screen at any frame. The bg.mp4 xfade still smooths the backdrop.
  const entry = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const exit = interpolate(frame, [Math.max(0, totalFrames - 8), totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const segmentOpacity = entry * exit;

  // Split "Trivia by Travel Crush" into a styled lockup. Main word in Fraunces
  // italic (matches question text), attribution in tracked Montserrat
  // (matches choice text) with horizontal flourishes so it reads as a
  // magazine masthead / show title card, not plain uppercase text.
  const titleParts = title.split(/\s+by\s+/i);
  const mainWord = titleParts[0] || title;
  const byPart = titleParts[1] ? `by ${titleParts[1]}` : "";

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: 60, opacity: segmentOpacity }}>
      {/* Translucent dark scrim over the bg.mp4 hook backdrop so the
          show-identity text always reads against any image. */}
      <AbsoluteFill style={{ backgroundColor: COLORS.questionBg }} />

      {/* SHOW LOCKUP — two-line logo treatment */}
      <div style={{
        textAlign: "center",
        marginBottom: 50,
      }}>
        {/* Main word: big serif italic in gold — ties to question typography */}
        <div style={{
          fontFamily: serifFamily,
          fontStyle: "italic",
          fontWeight: 900,
          fontSize: 132,
          color: COLORS.hookText,
          lineHeight: 0.92,
          letterSpacing: -2,
          opacity: titleEnter,
          transform: `translateY(${interpolate(titleEnter, [0, 1], [40, 0])}px)`,
          textShadow: "0 6px 22px rgba(0, 0, 0, 0.6)",
        }}>
          {mainWord}
        </div>

        {/* Attribution row: horizontal rule · tracked caps · horizontal rule */}
        {byPart && (
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 18,
            marginTop: 22,
            opacity: subtitleEnter,
            transform: `translateY(${interpolate(subtitleEnter, [0, 1], [16, 0])}px)`,
          }}>
            <div style={{
              height: 2,
              width: 56,
              backgroundColor: "rgba(255, 255, 255, 0.55)",
            }} />
            <span style={{
              fontFamily: sansFamily,
              fontWeight: 700,
              fontSize: 30,
              color: "rgba(255, 255, 255, 0.92)",
              letterSpacing: 7,
              textTransform: "uppercase",
              whiteSpace: "nowrap",
            }}>
              {byPart}
            </span>
            <div style={{
              height: 2,
              width: 56,
              backgroundColor: "rgba(255, 255, 255, 0.55)",
            }} />
          </div>
        )}
      </div>
      {/* Hook hero text — sits inside a TextCard so contrast holds when an
          OpenArt clip plays behind it in v0.2. The hourglass emoji was
          removed: it sprung in late and then hard-cut at segment end,
          reading as a flicker rather than a flourish. */}
      <TextCard maxWidth={960}>
        <div style={{
          fontFamily,
          fontWeight: 900,
          fontSize: 120,
          color: "#FFFFFF",
          textAlign: "center",
          lineHeight: 1.05,
          opacity: hookEnter,
          transform: `scale(${interpolate(hookEnter, [0, 1], [0.85, 1])})`,
          // paint-order ensures the stroke renders BEHIND the fill so it shows
          // as a clean outline instead of eating into letter counters (e.g.
          // the closed spaces in 'a', 'e', '%'). Default order paints stroke
          // ON TOP, which produces spotty artifacts inside letters at this size.
          WebkitTextStroke: `5px ${COLORS.hookText}`,
          paintOrder: "stroke fill",
        }}>
          {hook}
        </div>
      </TextCard>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// CountdownBar — horizontal bar that drains over countdown_duration_s
// ---------------------------------------------------------------------------

const CountdownBar: React.FC<{ durationS: number }> = ({ durationS }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.ceil(durationS * fps);
  const progress = interpolate(frame, [0, totalFrames], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const fillColor =
    progress < 0.5 ? COLORS.countdownSafe :
    progress < 0.85 ? COLORS.countdownWarn :
    COLORS.countdownDanger;

  // Subtle pulse in the danger zone
  const dangerPulse = progress >= 0.85 ? 1 + Math.sin(frame * 0.6) * 0.04 : 1;

  return (
    <div style={{
      position: "absolute",
      bottom: 220,
      left: 80,
      right: 80,
      height: 56,
      borderRadius: 28,
      backgroundColor: "rgba(255, 255, 255, 0.18)",
      overflow: "hidden",
      border: "3px solid rgba(255, 255, 255, 0.35)",
      transform: `scaleY(${dangerPulse})`,
    }}>
      <div style={{
        width: `${(1 - progress) * 100}%`,
        height: "100%",
        backgroundColor: fillColor,
        transition: "background-color 0.1s linear",
        borderRadius: 25,
      }} />
    </div>
  );
};

// ---------------------------------------------------------------------------
// QuestionCard — full segment (question text + countdown + reveal + surprise fact)
// ---------------------------------------------------------------------------

const QuestionCard: React.FC<{ q: QuizQuestion; totalFrames: number }> = ({ q, totalFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Pre-reveal: show question + choices + countdown
  // Post-reveal: highlight correct answer + surprise fact appears
  const revealFrame = Math.round(q.reveal_at_s * fps);
  const isRevealed = frame >= revealFrame;
  const revealedFor = Math.max(0, frame - revealFrame);

  // Quick entry + exit fades (~8 frames each). Sequences are now back-to-back
  // (no time overlap between segments), so only ONE QuestionCard renders at
  // any frame — the entry fade ramps the new card up while the bg.mp4 xfades
  // underneath. No more two-cards-stacked-on-each-other.
  const entry = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const exit = interpolate(frame, [Math.max(0, totalFrames - 8), totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const segmentOpacity = entry * exit;

  const questionEnter = spring({ frame, fps, durationInFrames: 12, config: { damping: 14 } });
  // Stagger choices in one-by-one for a touch of game-show pacing.
  const choiceEnterFor = (i: number) =>
    spring({ frame: frame - (6 + i * 4), fps, durationInFrames: 14, config: { damping: 12 } });

  const letterColors = [COLORS.letterBadgeA, COLORS.letterBadgeB, COLORS.letterBadgeC];

  // Strip the "A) " prefix from each choice so the LetterBadge owns the
  // letter and the row text is just the answer. "B) Sudan" -> { letter: "B", text: "Sudan" }
  const splitChoice = (choice: string, fallbackIdx: number): { letter: string; text: string } => {
    const m = choice.match(/^\s*([A-Z])\)\s*(.+)$/);
    if (m) return { letter: m[1], text: m[2] };
    return { letter: ["A", "B", "C", "D"][fallbackIdx] || "?", text: choice };
  };

  return (
    <AbsoluteFill style={{ opacity: segmentOpacity }}>
      {/* The OpenArt Seedance clip plays through from bg.mp4 — no BrandBackdrop
          on question segments. Just a translucent dark scrim to keep text
          contrast safe across whatever the clip's color palette ends up being. */}
      <AbsoluteFill style={{ backgroundColor: COLORS.questionBg }} />

      {/* Difficulty stamp — angled passport-stamp badge at top */}
      <div style={{
        position: "absolute",
        top: 130,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
      }}>
        <DifficultyStamp qid={q.id} difficulty={q.difficulty} />
      </div>

      {/* Question text — Fraunces italic serif, wrapped in a TextCard so
          the serif stays readable when the v0.2 OpenArt clip plays behind. */}
      <div style={{
        position: "absolute",
        top: 340,
        left: 56,
        right: 56,
        display: "flex",
        justifyContent: "center",
        opacity: questionEnter,
        transform: `translateY(${interpolate(questionEnter, [0, 1], [30, 0])}px)`,
      }}>
        <TextCard maxWidth={960} padding="32px 40px">
          <div style={{
            fontFamily: serifFamily,
            fontStyle: "italic",
            fontWeight: 900,
            fontSize: 72,
            color: COLORS.questionSerif,
            textAlign: "center",
            lineHeight: 1.1,
            letterSpacing: -0.5,
          }}>
            {q.question}
          </div>
        </TextCard>
      </div>

      {/* Choices — letter-badge layout. Each row: big colored circle (A/B/C)
          on the left, bold answer text next to it. On reveal: badge ring goes
          green + passport stamp slams in. */}
      <div style={{
        position: "absolute",
        top: 1050,
        left: 64,
        right: 64,
        display: "flex",
        flexDirection: "column",
        gap: 22,
      }}>
        {q.choices.map((choice, i) => {
          const { letter, text } = splitChoice(choice, i);
          const isCorrect = i === q.answer_index;
          const showRevealStyle = isRevealed && isCorrect;
          const stampEnter = showRevealStyle
            ? spring({ frame: revealedFor, fps, durationInFrames: 14, config: { damping: 11, stiffness: 200 } })
            : 0;
          const enter = choiceEnterFor(i);
          const rowScale = showRevealStyle ? interpolate(stampEnter, [0, 1], [1, 1.03]) : 1;

          return (
            <div key={i} style={{
              position: "relative",
              display: "flex",
              alignItems: "center",
              gap: 24,
              padding: "16px 30px 16px 16px",
              borderRadius: 26,
              backgroundColor: showRevealStyle ? COLORS.revealCorrect : COLORS.letterBadgeBg,
              border: `3px solid ${showRevealStyle ? "#3b8a00" : "rgba(255, 255, 255, 0.10)"}`,
              opacity: enter,
              transform: `translateX(${interpolate(enter, [0, 1], [-40, 0])}px) scale(${rowScale})`,
              backdropFilter: showRevealStyle ? "none" : "blur(3px)",
              boxShadow: showRevealStyle
                ? "0 10px 28px rgba(88, 204, 2, 0.55), inset 0 -6px 0 rgba(0, 0, 0, 0.15)"
                : "0 4px 12px rgba(0, 0, 0, 0.25)",
            }}>
              <LetterBadge letter={letter} color={letterColors[i % letterColors.length]} showRevealStyle={showRevealStyle} />
              <span style={{
                flex: 1,
                fontFamily: sansFamily,
                fontWeight: 900,
                fontSize: 60,
                color: showRevealStyle ? COLORS.revealStroke : COLORS.questionText,
                letterSpacing: -0.5,
                textShadow: showRevealStyle ? "none" : "0 2px 6px rgba(0,0,0,0.45)",
              }}>
                {text}
              </span>
              {showRevealStyle && <PassportStamp delayFrames={2} />}
            </div>
          );
        })}
      </div>

      {/* Surprise fact, after reveal. Pass remaining frames so it can fade
          out smoothly before the segment hard-cuts. */}
      {isRevealed && (
        <Sequence from={revealFrame + 8} durationInFrames={Math.max(1, Math.round(q.duration_s * fps) - revealFrame - 8)}>
          <SurpriseFact
            text={q.surprise_fact}
            remainingFrames={Math.round(q.duration_s * fps) - revealFrame - 8}
          />
        </Sequence>
      )}

      {/* Countdown bar — only shown pre-reveal */}
      {!isRevealed && (
        <Sequence from={Math.round(q.countdown_start_s * fps)} durationInFrames={Math.round(q.countdown_duration_s * fps)}>
          <CountdownBar durationS={q.countdown_duration_s} />
        </Sequence>
      )}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// SurpriseFact — 1-line yellow caption near bottom after reveal
// ---------------------------------------------------------------------------

const SurpriseFact: React.FC<{ text: string; remainingFrames: number }> = ({ text, remainingFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, durationInFrames: 14, config: { damping: 12 } });
  // Fade out smoothly over the last 12 frames before the segment hard-cuts,
  // so the text doesn't appear to "vanish" when the next segment takes over.
  const exit = interpolate(
    frame,
    [Math.max(0, remainingFrames - 12), remainingFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <div style={{
      position: "absolute",
      bottom: 150,
      left: 80,
      right: 80,
      display: "flex",
      justifyContent: "center",
      opacity: enter * exit,
      transform: `translateY(${interpolate(enter, [0, 1], [20, 0])}px)`,
    }}>
      <TextCard maxWidth={920} padding="20px 34px">
        <div style={{
          fontFamily,
          fontWeight: 700,
          fontSize: 44,
          color: COLORS.surpriseFact,
          textAlign: "center",
          lineHeight: 1.25,
        }}>
          {text}
        </div>
      </TextCard>
    </div>
  );
};

// ---------------------------------------------------------------------------
// ScoreCard — segment 28–32s
// ---------------------------------------------------------------------------

const ScoreCard: React.FC<{
  closer: { intro: string; emphasis: string; cta: string };
  lockupText: string;
  lockupBrand: string;
  placeholderUrl: string;
  bottomCta: string;
  reward: string;
}> = ({ closer, lockupText, lockupBrand, placeholderUrl, bottomCta, reward }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Entry fade matching the other cards. No exit fade — score is the final
  // beat of the video; let it stay visible until the comp ends.
  const entry = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const ctaEnter = spring({ frame, fps, durationInFrames: 14, config: { damping: 13 } });
  const lockupEnter = spring({ frame: frame - 16, fps, durationInFrames: 18, config: { damping: 12 } });
  const bottomCtaEnter = spring({ frame: frame - 30, fps, durationInFrames: 16, config: { damping: 13 } });

  return (
    <AbsoluteFill style={{
      padding: 60,
      display: "flex",
      flexDirection: "column",
      justifyContent: "center",
      alignItems: "center",
      gap: 110,                 // explicit breathing room between lockup + CTA
      opacity: entry,
    }}>
      {/* Translucent dark scrim over the bg.mp4 score backdrop so the
          sponsor lockup and CTA always read against any image. */}
      <AbsoluteFill style={{ backgroundColor: COLORS.questionBg }} />
      {/* Top: removed — viewer goes straight to the sponsor lockup. */}

      {/* Center: Sponsored-by lockup — "SPONSORED BY" label + Travel Crush
          logo only. No "Play in bio", no URL. The CTA below carries the
          action; this card carries the brand attribution. */}
      <div style={{
        backgroundColor: "#FFFFFF",
        padding: "32px 64px 40px",
        borderRadius: 28,
        textAlign: "center",
        opacity: lockupEnter,
        transform: `translateY(${interpolate(lockupEnter, [0, 1], [40, 0])}px)`,
        boxShadow: "0 10px 36px rgba(255, 106, 44, 0.55)",
        border: `4px solid ${COLORS.brandOrange}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 14,
      }}>
        <div style={{
          fontFamily,
          fontWeight: 800,
          fontSize: 28,
          color: "#94A3B8",
          letterSpacing: 6,
          textTransform: "uppercase",
        }}>
          Sponsored by
        </div>
        <Img
          src={staticFile("tc_logo.png")}
          style={{ width: 540, height: "auto", display: "block" }}
        />
      </div>

      {/* Bottom: tomorrow tease + optional reward — wrapped in TextCard for
          contrast against future OpenArt backdrops. */}
      <div style={{
        marginBottom: 220,
        opacity: bottomCtaEnter,
      }}>
        <TextCard padding="22px 42px">
          <div style={{
            fontFamily,
            fontWeight: 700,
            fontSize: 46,
            color: COLORS.hookText,
            textAlign: "center",
          }}>
            {bottomCta}
          </div>
          {reward && (
            <div style={{
              fontFamily,
              fontWeight: 700,
              fontSize: 36,
              marginTop: 14,
              color: "rgba(255, 255, 255, 0.85)",
              textAlign: "center",
            }}>
              🏆 {reward}
            </div>
          )}
        </TextCard>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// TriviaQuiz — top-level composition
// ---------------------------------------------------------------------------

export const TriviaQuiz: React.FC<TriviaQuizProps> = ({ videoSrc, meta }) => {
  const { fps } = useVideoConfig();

  const scoreStart = Math.round(meta.score_card.start_s * fps);

  // No hook intro — Q1 runs from frame 0. Sequence boundaries stay
  // back-to-back so only one card UI is ever on screen.
  const segStarts = [
    Math.round(meta.questions[0].start_s * fps),
    Math.round(meta.questions[1].start_s * fps),
    Math.round(meta.questions[2].start_s * fps),
    scoreStart,
  ];
  const qDurations = [
    segStarts[1] - segStarts[0],
    segStarts[2] - segStarts[1],
    segStarts[3] - segStarts[2],
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.gradientTop }}>
      {/* Background video — runs the full length, includes the bg.mp4 xfade
          transitions baked in by assemble_quiz.py. */}
      {videoSrc && (
        <OffthreadVideo src={videoSrc} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      )}

      {/* No hook card — viewer goes straight to Q1 at frame 0 */}

      {/* Three question cards — each ends exactly when the next segment starts */}
      {meta.questions.map((q, i) => {
        const from = segStarts[i];
        const duration = qDurations[i];
        return (
          <Sequence key={q.id} from={from} durationInFrames={duration} layout="none">
            <QuestionCard q={q} totalFrames={duration} />
          </Sequence>
        );
      })}

      {/* Score card — final segment */}
      <Sequence from={scoreStart}>
        <ScoreCard
          closer={meta.show.closer}
          lockupText={meta.show.lockup_text}
          lockupBrand={meta.show.lockup_brand}
          placeholderUrl={meta.show.placeholder_url}
          bottomCta={meta.score_card.bottom_cta}
          reward={meta.score_card.reward}
        />
      </Sequence>
    </AbsoluteFill>
  );
};
