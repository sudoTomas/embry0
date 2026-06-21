// Capture the first decoded frame of a `getDisplayMedia` stream as a PNG Blob.
//
// Two prior assays found races here that have to stay fixed:
//   1. `loadedmetadata` only guarantees video dimensions, not a decoded frame —
//      drawing immediately after it can produce a blank canvas. We wait for
//      `loadeddata` instead, then optionally one `requestVideoFrameCallback`
//      tick to be doubly safe on browsers that support it.
//   2. If `srcObject` is assigned BEFORE the readiness listener is registered,
//      a fast event can be missed and capture will hang. We always attach the
//      listener first.
//
// We also stop every MediaStream track in a `finally` so the browser's
// recording indicator disappears even when capture throws.

export async function captureScreen(): Promise<Blob> {
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: { frameRate: 1 },
    audio: false,
  });
  try {
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;

    // Attach listeners BEFORE setting srcObject so a fast `loadeddata` event
    // can never slip past us.
    const ready = new Promise<void>((resolve, reject) => {
      const onReady = () => {
        video.removeEventListener("loadeddata", onReady);
        video.removeEventListener("error", onError);
        resolve();
      };
      const onError = () => {
        video.removeEventListener("loadeddata", onReady);
        video.removeEventListener("error", onError);
        reject(new Error("video readiness error"));
      };
      video.addEventListener("loadeddata", onReady);
      video.addEventListener("error", onError);
    });

    video.srcObject = stream;
    await video.play();
    await ready;

    // Belt-and-braces: on browsers that support it, also wait one
    // requestVideoFrameCallback tick after `loadeddata` so the frame is
    // guaranteed decoded and presented before we draw to canvas.
    const rvfc = (video as { requestVideoFrameCallback?: (cb: () => void) => number })
      .requestVideoFrameCallback;
    if (typeof rvfc === "function") {
      await new Promise<void>((resolve) => {
        rvfc.call(video, () => resolve());
      });
    }

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 1;
    canvas.height = video.videoHeight || 1;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("canvas 2d context unavailable");
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((b) => {
        if (b) resolve(b);
        else reject(new Error("canvas toBlob returned null"));
      }, "image/png");
    });
  } finally {
    for (const track of stream.getTracks()) {
      track.stop();
    }
  }
}
