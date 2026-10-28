/**
 * Nightfall (spec 2026-10-27, art direction): the world after dark, so its
 * pixel art sits inside the dashboard's black instead of glowing against
 * it. The art is re-lit, not redrawn: one colour matrix takes every sheet
 * toward the page's black (saturation to 45 %, light to 58 %, shadows
 * leaning blue-black), and gold light falls where the work is.
 *
 * The matrix is applied two ways, so both read alike: as an SVG filter
 * (``#nightfall``, drawn once by <NightfallFilter />, Nightfall.tsx) on every sprite of a
 * scene, and as a pixel pass over a scene's terrain canvas once it is
 * painted. Effects (experience, a level up, a hit marker, loot) are light and
 * stay as drawn.
 */

const SATURATION = 0.45;
const LIGHT = 0.58;
/** The night's tint: red and green a little down, blue up, and a floor of blue in the shadows. */
const GAIN: readonly [number, number, number] = [0.92, 0.94, 1.18];
const BLUE_FLOOR = 0.03;
/** sRGB luma, the grey a colour keeps when it loses its saturation. */
const LUMA: readonly [number, number, number] = [0.2126, 0.7152, 0.0722];

function nightMatrix(): number[][] {
  const rows: number[][] = [];
  for (let out = 0; out < 3; out++) {
    const row = [0, 1, 2].map((inp) => GAIN[out] * LIGHT * ((1 - SATURATION) * LUMA[inp] + (inp === out ? SATURATION : 0)));
    rows.push([...row, 0, out === 2 ? BLUE_FLOOR : 0]);
  }
  rows.push([0, 0, 0, 1, 0]);
  return rows;
}

/** The 4×5 colour matrix, rows R, G, B, A over inputs R, G, B, A and a constant. */
export const NIGHT_MATRIX: readonly (readonly number[])[] = nightMatrix();

/** The id of the filter <NightfallFilter /> draws. */
export const NIGHTFALL_FILTER_ID = "nightfall";

/** The matrix as feColorMatrix's ``values``. */
export function nightMatrixValues(): string {
  return NIGHT_MATRIX.flat()
    .map((v) => Number(v.toFixed(5)).toString())
    .join(" ");
}

/** Re-lights RGBA pixels in place (a canvas's ImageData), alpha untouched. */
export function relightPixels(data: Uint8ClampedArray): void {
  const [r0, g0, b0] = NIGHT_MATRIX;
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] / 255;
    const g = data[i + 1] / 255;
    const b = data[i + 2] / 255;
    data[i] = 255 * (r0[0] * r + r0[1] * g + r0[2] * b + r0[4]);
    data[i + 1] = 255 * (g0[0] * r + g0[1] * g + g0[2] * b + g0[4]);
    data[i + 2] = 255 * (b0[0] * r + b0[1] * g + b0[2] * b + b0[4]);
  }
}

/** Re-lights what a canvas holds, once it is painted; a canvas the browser will not read back stays as it is. */
export function relightCanvas(ctx: CanvasRenderingContext2D, width: number, height: number): void {
  if (width <= 0 || height <= 0) return;
  try {
    const image = ctx.getImageData(0, 0, width, height);
    relightPixels(image.data);
    ctx.putImageData(image, 0, 0);
  } catch {
    // A tainted canvas or a test DOM without pixels: the scene keeps its daylight.
  }
}
