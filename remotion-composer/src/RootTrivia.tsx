import { Composition, CalculateMetadataFunction, staticFile } from "remotion";
import {
  TriviaWithBg,
  TriviaWithBgProps,
  TriviaMode,
  ChoicesOption,
} from "./TriviaWithBg";

/**
 * Trivia-pipeline Root. Lives separately from the core src/Root.tsx so its
 * per-project JSON inputs (public/words.json, public/meta.json, etc.) load at
 * RENDER time via calculateMetadata rather than as static module imports.
 * That isolation means a missing JSON for one trivia render no longer breaks
 * the bundle for every other composition in the project.
 *
 * Render entry point: src/index-trivia.tsx -> registerRoot(RootTrivia).
 *
 * Usage:
 *   npx remotion render src/index-trivia.tsx TriviaWithBg <out.mp4>
 */

// ---------------------------------------------------------------------------
// Shared types for the meta.json schema written by assemble_modular.py
// ---------------------------------------------------------------------------

interface TriviaMetaFile {
  mode: TriviaMode;
  options: string[];
  option_reveal_times_s: number[];
  suppress_captions_window_ms: [number, number] | null;
  // Resolution → CTA boundary. The renderer uses `cta_text`'s first word
  // to find the actual CTA-start position in the transcript (Whisper's
  // word boundaries can drift hundreds of ms from the nominal timeline).
  // `cta_nominal_start_ms` is the lower bound for that search.
  cta_text?: string | null;
  cta_nominal_start_ms?: number | null;
  // Optional render overrides used by other pipelines that reuse TriviaWithBg
  // (e.g. trivia-reaction). Trivia-short omits these and gets the defaults.
  duration_s?: number;
  highlight_color?: string;
  show_facts_overlay?: boolean;
  base_color?: string;
  font_size?: number;
  dark_overlay?: number;
}

type WordEntry = { word: string; startMs: number; endMs: number };

const fetchJson = async <T,>(filename: string): Promise<T> => {
  const url = staticFile(filename);
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`Failed to load ${filename} (${r.status} ${r.statusText})`);
  }
  return (await r.json()) as T;
};

// ---------------------------------------------------------------------------
// TriviaWithBg — loads words.json + meta.json at render time
// ---------------------------------------------------------------------------

const calculateTriviaWithBgMetadata: CalculateMetadataFunction<
  TriviaWithBgProps
> = async ({ props }) => {
  const words = await fetchJson<WordEntry[]>("words.json");
  let mode: TriviaMode = "Facts";
  let options: ChoicesOption[] = [];
  let suppressCaptionsWindowMs: [number, number] | null = null;
  let ctaText: string | null = null;
  let ctaNominalStartMs: number | null = null;
  let meta: TriviaMetaFile | null = null;

  try {
    meta = await fetchJson<TriviaMetaFile>("meta.json");
    mode = meta.mode;
    suppressCaptionsWindowMs = meta.suppress_captions_window_ms;
    ctaText = meta.cta_text ?? null;
    ctaNominalStartMs = meta.cta_nominal_start_ms ?? null;
    if (meta.mode === "Choices") {
      options = meta.options.map((label, i) => ({
        label,
        revealAtSec: meta.option_reveal_times_s[i] ?? 3 + i * 1.5,
      }));
    }
  } catch {
    // Facts mode never writes meta.json — silently fall back to defaults.
  }

  // Optional per-pipeline overrides from meta.json. Trivia-short omits all
  // of these and the composition's defaultProps / hardcoded duration win.
  // Trivia-reaction sets duration_s + show_facts_overlay=false + highlight_color
  // so its 15s+ clips don't get truncated to 13.4s.
  const fps = 30;
  const durationInFrames = meta?.duration_s
    ? Math.ceil(meta.duration_s * fps)
    : undefined;

  return {
    ...(durationInFrames ? { durationInFrames } : {}),
    props: {
      ...props,
      videoSrc: staticFile("bg.mp4"),
      words,
      mode,
      options,
      suppressCaptionsWindowMs,
      ctaText,
      ctaNominalStartMs,
      // Override defaultProps from meta.json when present.
      ...(meta?.highlight_color    ? { highlightColor: meta.highlight_color } : {}),
      ...(meta?.base_color         ? { baseColor: meta.base_color } : {}),
      ...(meta?.font_size != null  ? { fontSize: meta.font_size } : {}),
      ...(meta?.dark_overlay != null ? { darkOverlay: meta.dark_overlay } : {}),
      ...(meta?.show_facts_overlay != null
        ? { showFactsOverlay: meta.show_facts_overlay }
        : {}),
    },
  };
};

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export const RootTrivia: React.FC = () => (
  <>
    <Composition
      id="TriviaWithBg"
      component={TriviaWithBg}
      durationInFrames={Math.ceil(13.4 * 30)}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={{
        videoSrc: "",
        words: [],
        darkOverlay: 0,
        highlightColor: "#22E88A",
        baseColor: "#FFFFFF",
        fontSize: 78,
        showFactsOverlay: true,
        mode: "Facts" as TriviaMode,
        options: [] as ChoicesOption[],
        suppressCaptionsWindowMs: null,
        ctaText: null,
        ctaNominalStartMs: null,
      }}
      calculateMetadata={calculateTriviaWithBgMetadata}
    />
  </>
);
