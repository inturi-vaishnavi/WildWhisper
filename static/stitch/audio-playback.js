(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WildWhisperAudio = api;
})(typeof globalThis === "undefined" ? this : globalThis, function () {
  const attempts = new WeakMap();

  function autoPlay(audio, { onBlocked, onError } = {}) {
    const existingAttempt = attempts.get(audio);
    if (existingAttempt) return existingAttempt;

    const attempt = new Promise(resolve => {
      let settled = false;
      const cleanup = () => {
        audio.removeEventListener("canplay", onReady);
        audio.removeEventListener("canplaythrough", onReady);
        audio.removeEventListener("error", onLoadError);
      };
      const finish = played => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(played);
      };
      const onLoadError = () => {
        onError?.();
        finish(false);
      };
      const onReady = () => {
        if (audio.readyState < (audio.HAVE_FUTURE_DATA || 3)) return;
        cleanup();
        if (!audio.paused) {
          finish(true);
          return;
        }
        Promise.resolve().then(() => audio.play()).then(
          () => finish(true),
          error => {
            if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
              onBlocked?.();
            } else {
              onError?.();
            }
            finish(false);
          }
        );
      };

      if (!audio.src) {
        onLoadError();
        return;
      }
      audio.addEventListener("canplay", onReady);
      audio.addEventListener("canplaythrough", onReady);
      audio.addEventListener("error", onLoadError);
      try {
        audio.load();
      } catch {
        onLoadError();
        return;
      }
      if (audio.readyState >= (audio.HAVE_FUTURE_DATA || 3)) onReady();
    });

    attempts.set(audio, attempt);
    return attempt;
  }

  return { autoPlay };
});
