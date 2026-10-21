import { Component, useContext, useState, type ReactNode } from "react";
import { motionIndex } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { GlideContext, useGlideRef, type Glide } from "@/hooks/useGlide";

interface GlideBoardProps {
  glide: Glide;
  /** What the board shows. A new value is a poll that may move cards. */
  layout: unknown;
  /** The board's filters. A new value is a fresh paint: nothing glides. */
  paintKey: string;
  children: ReactNode;
}

/**
 * The board's cards glide from where they were to where a poll put them.
 * A class because getSnapshotBeforeUpdate is React's one hook that runs
 * after the render and before the DOM changes, which is the moment the old
 * places can still be read.
 */
export class GlideBoard extends Component<GlideBoardProps> {
  getSnapshotBeforeUpdate(prev: GlideBoardProps): null {
    if (prev.paintKey !== this.props.paintKey) this.props.glide.reset();
    else if (prev.layout !== this.props.layout) this.props.glide.snapshot();
    return null;
  }

  componentDidMount(): void {
    this.props.glide.play();
  }

  componentDidUpdate(): void {
    this.props.glide.play();
  }

  render() {
    return <GlideContext.Provider value={this.props.glide}>{this.props.children}</GlideContext.Provider>;
  }
}

interface GlideItemProps {
  /** The job the card shows: its place on the board is kept under this id. */
  id: string;
  /** Its place in the lane, the turn it takes when cards arrive together. */
  index: number;
  children: ReactNode;
}

/**
 * One card's place on the board. A card the board has not shown before
 * arrives (.motion-enter); a card that was elsewhere glides from there
 * instead, so a job moving lane is seen moving rather than appearing twice.
 */
export function GlideItem({ id, index, children }: GlideItemProps) {
  const glide = useContext(GlideContext);
  const [arriving] = useState(() => !glide?.knows(id));
  const ref = useGlideRef(id);
  return (
    <div
      ref={ref}
      // The page's own colour under the card: cards are see-through, and one
      // travelling over another must not show both at once.
      className={cn("rounded-lg bg-background", arriving && "motion-enter")}
      style={arriving ? motionIndex(index) : undefined}
    >
      {children}
    </div>
  );
}
