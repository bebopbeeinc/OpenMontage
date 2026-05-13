import { registerRoot } from "remotion";
import { RootTrivia } from "./RootTrivia";

// Entry point for the trivia pipeline. Kept separate from src/index.tsx so
// trivia's per-project JSON inputs only load when trivia is being rendered.
// See RootTrivia.tsx for the calculateMetadata details.
registerRoot(RootTrivia);
