import { registerRoot, Composition, staticFile } from "remotion";
import { TriviaTwoTruthsK3 } from "./TriviaTwoTruthsK3";

const Root: React.FC = () => (
  <Composition
    id="TriviaTwoTruthsK3"
    component={TriviaTwoTruthsK3}
    durationInFrames={Math.ceil(15.1 * 30)}
    fps={30}
    width={1080}
    height={1920}
    defaultProps={{
      videoSrc: staticFile("2t1l_bg.mp4"),
      logoSrc: staticFile("tc_logo.png"),
      title: "2 TRUTHS, 1 LIE",
      themeName: "neon" as const,
      claims: [
        { label: "Swimming pigs", revealAtSec: 2.0 },
        { label: "600ft blue hole", revealAtSec: 5.0 },
        { label: "Blue sand", revealAtSec: 7.7 },
      ],
      words: [],
      highlightColor: "#D63B2F",
      baseColor: "#FFFFFF",
      fontSize: 78,
    }}
  />
);

registerRoot(Root);
