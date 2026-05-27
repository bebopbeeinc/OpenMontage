import { registerRoot } from "remotion";
import { RootTriviaQuiz } from "./RootTriviaQuiz";

// Entry point for the trivia-quiz pipeline. Kept separate from src/index.tsx
// and src/index-trivia.tsx so each pipeline's per-project JSON inputs only
// load when that pipeline is being rendered.
//
// Usage:
//   npx remotion render src/index-trivia-quiz.tsx TriviaQuiz <out.mp4>
registerRoot(RootTriviaQuiz);
