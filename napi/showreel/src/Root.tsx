import { Composition } from "remotion";
import { Reel, REEL_FRAMES } from "./Reel";

export const RemotionRoot: React.FC = () => (
  <Composition id="NapiReel" component={Reel} durationInFrames={REEL_FRAMES} fps={30} width={1920} height={1080} />
);
