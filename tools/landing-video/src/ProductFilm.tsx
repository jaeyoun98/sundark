import type { CSSProperties } from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  OffthreadVideo,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type Clip = "translation" | "anatomy" | "chat";
type Crop = { x: number; y: number; width: number; height: number };
const blue = "#243dbe";
const ease = Easing.bezier(0.22, 1, 0.36, 1);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease } as const;
const details: Record<Clip, readonly [Crop, Crop]> = {
  translation: [
    { x: 315, y: 450, width: 500, height: 340 },
    { x: 950, y: 155, width: 640, height: 435 },
  ],
  anatomy: [
    { x: 960, y: 545, width: 630, height: 430 },
    { x: 320, y: 450, width: 500, height: 340 },
  ],
  chat: [
    { x: 320, y: 415, width: 500, height: 340 },
    { x: 960, y: 320, width: 630, height: 430 },
  ],
};

// Every view uses the same capture and source time. These are editorial crops,
// never recreated UI or replacements for the app's highlights and answers.
const Footage = ({ clip, style }: { clip: Clip; style?: CSSProperties }) => (
  <OffthreadVideo
    muted
    src={staticFile(`inputs/source-${clip}.mp4`)}
    style={{ position: "absolute", width: 1600, height: 1000, maxWidth: "none", ...style }}
  />
);

const Brand = () => (
  <div
    style={{
      position: "absolute",
      top: 27,
      left: 54,
      display: "flex",
      alignItems: "center",
      gap: 11,
      color: "#182141",
    }}
  >
    <div
      style={{
        width: 28,
        height: 28,
        borderRadius: "50% 50% 7px 50%",
        background: blue,
        display: "grid",
        placeItems: "center",
        color: "#fff",
        fontFamily: "Georgia, serif",
        fontSize: 26,
        fontStyle: "italic",
        lineHeight: 1,
      }}
    >
      s
    </div>
    <div style={{ fontFamily: "Arial, sans-serif", fontWeight: 700, fontSize: 23, letterSpacing: -0.8 }}>Sundark</div>
  </div>
);

export const ProductFilm = ({ clip }: { clip: Clip }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = frame / fps;
  const enter = spring({
    frame,
    fps,
    durationInFrames: 36,
    config: { damping: 28, stiffness: 110, mass: 0.9, overshootClamping: true },
  });
  const detailStart = clip === "chat" ? 7.4 : 6.0;
  const detailEnd = clip === "chat" ? 18.1 : 13.8;
  const toDetails = interpolate(t, [detailStart, detailStart + 1.1], [0, 1], clamp);
  const fromDetails = interpolate(t, [detailEnd, detailEnd + 1.1], [0, 1], clamp);
  const split = toDetails * (1 - fromDetails);
  const camera = interpolate(t, [2.5, 5.6, detailStart + 1], [1, 1.055, 1.055], clamp);
  const stageScale = 0.975 + 0.025 * enter;
  const action = clip === "translation" ? 2.4 : clip === "anatomy" ? 3.1 : 1.8;
  const pulse = interpolate(t, [action, action + 0.25, action + 0.95], [0, 1, 0], clamp);
  const marker =
    clip === "translation" ? { x: 1250, y: 306 } : clip === "anatomy" ? { x: 1220, y: 665 } : { x: 1545, y: 930 };
  const panelOpacity = interpolate(split, [0, 0.85, 1], [1, 0.18, 0], clamp);

  return (
    <AbsoluteFill style={{ background: "#f4f6ff", overflow: "hidden" }}>
      <AbsoluteFill style={{ background: "linear-gradient(150deg, #ffffff 0%, #f4f6ff 50%, #e9eeff 100%)" }} />
      <Brand />
      {/* The two pages are the reading metaphor; no localized copy is burned in. */}
      <div
        style={{
          position: "absolute",
          left: 130,
          top: 144,
          width: 1350,
          height: 820,
          border: "1px solid #dce2f3",
          borderRadius: 22,
          background: "#fff",
          rotate: `${-1.8 * (1 - split)}deg`,
          opacity: 0.8,
          translate: "0px 3px",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 80,
          width: 1440,
          height: 900,
          perspective: 1800,
          scale: stageScale,
          translate: `0px ${(1 - enter) * 28}px`,
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            overflow: "hidden",
            borderRadius: 17,
            boxShadow: "0 28px 70px #18214124, 0 3px 12px #18214118",
            background: "#101010",
            opacity: panelOpacity,
            rotate: `${-0.5 * (1 - enter)}deg`,
            border: "1px solid #ffffff",
          }}
        >
          <div
            style={{
              position: "absolute",
              width: 1600,
              height: 1000,
              scale: `${0.9 * camera}`,
              transformOrigin: "0 0",
              translate: `${-30 * (camera - 1)}px ${-90 * (camera - 1)}px`,
            }}
          >
            <Footage clip={clip} />
            <div
              style={{
                position: "absolute",
                left: marker.x - 23,
                top: marker.y - 23,
                width: 46,
                height: 46,
                border: "2px solid #b7c5ff",
                borderRadius: "50%",
                opacity: pulse * 0.9,
                scale: 0.7 + pulse * 0.5,
                boxShadow: "0 0 0 7px #243dbe20",
              }}
            />
          </div>
        </div>
      </div>
      {details[clip].map((crop, i) => {
        const targetWidth = 700;
        const targetHeight = 480;
        const cropScale = Math.max(targetWidth / crop.width, targetHeight / crop.height);
        const targetX = 75 + i * 750;
        const targetY = 260 + (i === 0 ? -14 : 14);
        const reveal = interpolate(split, [0.05, 0.8], [0, 1], clamp);
        const drift = interpolate(t, [detailStart + 1, detailEnd], [0, i === 0 ? -6 : 6], clamp);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: targetX,
              top: targetY,
              width: targetWidth,
              height: targetHeight,
              borderRadius: 20,
              overflow: "hidden",
              opacity: reveal,
              background: i === 0 && clip !== "anatomy" ? "#fff" : "#101010",
              boxShadow: "0 24px 60px #18214120, 0 0 0 1px #d8deed",
              translate: `${(1 - split) * (i === 0 ? -60 : 60)}px ${(1 - split) * 42 + drift}px`,
              scale: 0.94 + 0.06 * split,
            }}
          >
            <div
              style={{
                position: "absolute",
                width: 1600,
                height: 1000,
                transformOrigin: "0 0",
                scale: cropScale,
                translate: `${-crop.x * cropScale}px ${-crop.y * cropScale}px`,
              }}
            >
              <Footage clip={clip} />
            </div>
            <div
              style={{
                position: "absolute",
                inset: 0,
                borderRadius: 20,
                border: "1px solid #ffffff40",
                pointerEvents: "none",
              }}
            />
          </div>
        );
      })}
      {/* A connector joins the genuine selected passage to its paired context. */}
      <svg width="1600" height="1000" style={{ position: "absolute", inset: 0, opacity: split }}>
        <path
          d="M 720 784 C 765 834 835 834 880 784"
          stroke={blue}
          strokeWidth="2"
          fill="none"
          strokeDasharray="180"
          strokeDashoffset={180 * (1 - toDetails)}
        />
        <circle cx="720" cy="784" r="5" fill={blue} />
        <circle cx="880" cy="784" r="5" fill={blue} />
      </svg>
      <div
        style={{
          position: "absolute",
          right: 55,
          top: 40,
          width: 116,
          height: 3,
          background: "#dce2f3",
          borderRadius: 2,
          overflow: "hidden",
        }}
      >
        <div style={{ width: `${(100 * frame) / (durationInFrames - 1)}%`, height: "100%", background: blue }} />
      </div>
    </AbsoluteFill>
  );
};
