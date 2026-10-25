import type { CSSProperties, ReactNode } from "react";
import { artUrl, type Rect } from "./manifest";
import { frameKeyframes } from "./style";

interface ArtSpriteProps {
  /** The sheet, relative to the art root. */
  image: string;
  /** The frame drawn when still, and the size of every frame. */
  rect: Rect;
  /** Frames to step through; one frame or none draws ``rect`` still. */
  frames?: readonly Rect[];
  /** A name unique to these frames, for their keyframes. */
  animation?: string;
  fps?: number;
  loop?: boolean;
  /** An integer: every pixel of the art becomes ``scale`` by ``scale`` screen pixels. */
  scale?: number;
  className?: string;
  style?: CSSProperties;
  title?: string;
  children?: ReactNode;
  onAnimationEnd?: () => void;
}

/** One frame of a sheet, or its frames stepping, at an integer scale: crisp, and as clickable as its box. */
export function ArtSprite({ image, rect, frames, animation, fps = 6, loop = true, scale = 1, className, style, title, children, onAnimationEnd }: ArtSpriteProps) {
  const [x, y, w, h] = rect;
  const animated = frames && frames.length > 1 && animation;
  const name = animated ? frameKeyframes(animation, frames) : null;
  return (
    <span className={className} style={{ display: "inline-block", position: "relative", width: w * scale, height: h * scale, ...style }} title={title}>
      <span
        className={name ? "art-anim" : undefined}
        onAnimationEnd={onAnimationEnd}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: w,
          height: h,
          transform: `scale(${scale})`,
          transformOrigin: "0 0",
          backgroundImage: `url(${artUrl(image)})`,
          backgroundPosition: `-${x}px -${y}px`,
          backgroundRepeat: "no-repeat",
          imageRendering: "pixelated",
          ...(name && frames
            ? { animation: `${name} ${(frames.length / fps).toFixed(3)}s steps(1, end) ${loop ? "infinite" : "1 forwards"}` }
            : {}),
        }}
      />
      {children}
    </span>
  );
}
