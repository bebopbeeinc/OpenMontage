import { Composition, CalculateMetadataFunction, staticFile } from "remotion";
import { TriviaQuiz, TriviaQuizProps, QuizMeta } from "./TriviaQuiz";

/**
 * Trivia-quiz pipeline Root. Sibling of RootTrivia (trivia-short pipeline).
 * Loads per-project JSON inputs (public/quiz_meta.json, public/bg.mp4) at
 * RENDER time via calculateMetadata, same isolation pattern as TriviaWithBg.
 *
 * Render entry point: src/index-trivia-quiz.tsx -> registerRoot(RootTriviaQuiz).
 *
 * Usage:
 *   npx remotion render src/index-trivia-quiz.tsx TriviaQuiz <out.mp4>
 */

const fetchJson = async <T,>(filename: string): Promise<T> => {
  const url = staticFile(filename);
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`Failed to load ${filename} (${r.status} ${r.statusText})`);
  }
  return (await r.json()) as T;
};

const calculateTriviaQuizMetadata: CalculateMetadataFunction<TriviaQuizProps> = async ({ props }) => {
  const meta = await fetchJson<QuizMeta>("quiz_meta.json");
  const fps = 30;

  // Total duration = score_card.start_s + 4s + 0.2s buffer for tail
  const totalDurationS = meta.score_card.start_s + 4.0 + 0.2;
  const durationInFrames = Math.ceil(totalDurationS * fps);

  return {
    durationInFrames,
    props: {
      ...props,
      videoSrc: staticFile("bg.mp4"),
      meta,
    },
  };
};

export const RootTriviaQuiz: React.FC = () => (
  <Composition
    id="TriviaQuiz"
    component={TriviaQuiz}
    durationInFrames={Math.ceil(32.0 * 30)}
    fps={30}
    width={1080}
    height={1920}
    defaultProps={{
      videoSrc: "",
      meta: {
        show: {
          title: "",
          hook: "",
          closer: { intro: "", emphasis: "", cta: "" },
          lockup_text: "",
          lockup_brand: "",
          placeholder_url: "",
        },
        questions: [],
        score_card: { start_s: 28.0, bottom_cta: "", reward: "", game_hook_line: "" },
      } as QuizMeta,
    }}
    calculateMetadata={calculateTriviaQuizMetadata}
  />
);
