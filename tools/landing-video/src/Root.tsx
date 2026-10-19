import { Composition } from "remotion";
import { ProductFilm } from "./ProductFilm";

export const Root = () => (
  <>
    <Composition
      id="Translation"
      component={ProductFilm}
      durationInFrames={540}
      fps={30}
      width={1600}
      height={1000}
      defaultProps={{ clip: "translation" as const }}
    />
    <Composition
      id="Anatomy"
      component={ProductFilm}
      durationInFrames={540}
      fps={30}
      width={1600}
      height={1000}
      defaultProps={{ clip: "anatomy" as const }}
    />
    <Composition
      id="Chat"
      component={ProductFilm}
      durationInFrames={675}
      fps={30}
      width={1600}
      height={1000}
      defaultProps={{ clip: "chat" as const }}
    />
  </>
);
