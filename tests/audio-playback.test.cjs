const test = require("node:test");
const assert = require("node:assert/strict");
const { autoPlay } = require("../static/stitch/audio-playback.js");

class MockAudio extends EventTarget {
  constructor(src = "data:audio/mpeg;base64,dGVzdA==") {
    super();
    this.src = src;
    this.readyState = 0;
    this.HAVE_FUTURE_DATA = 3;
    this.paused = true;
    this.loadCalls = 0;
    this.playCalls = 0;
    this.playBehavior = () => Promise.resolve();
  }

  load() {
    this.loadCalls += 1;
  }

  play() {
    this.playCalls += 1;
    return this.playBehavior().then(() => {
      this.paused = false;
    });
  }

  makeReady() {
    this.readyState = this.HAVE_FUTURE_DATA;
    this.dispatchEvent(new Event("canplay"));
  }
}

test("waits for audio readiness and starts playback once", async () => {
  const audio = new MockAudio();
  const attempt = autoPlay(audio);

  assert.equal(audio.loadCalls, 1);
  assert.equal(audio.playCalls, 0);
  audio.makeReady();

  assert.equal(await attempt, true);
  assert.equal(audio.playCalls, 1);
});

test("uses one automatic playback attempt when requested repeatedly", async () => {
  const audio = new MockAudio();
  const first = autoPlay(audio);
  const second = autoPlay(audio);

  assert.equal(first, second);
  assert.equal(audio.loadCalls, 1);
  audio.makeReady();
  assert.equal(await first, true);
  assert.equal(audio.playCalls, 1);
});

test("reports autoplay restrictions and leaves playback available for manual retry", async () => {
  const audio = new MockAudio();
  let blocked = 0;
  audio.playBehavior = () => Promise.reject(
    Object.assign(new Error("Autoplay blocked"), { name: "NotAllowedError" })
  );
  const attempt = autoPlay(audio, { onBlocked: () => { blocked += 1; } });
  audio.makeReady();

  assert.equal(await attempt, false);
  assert.equal(blocked, 1);
  audio.playBehavior = () => Promise.resolve();
  await audio.play();
  assert.equal(audio.playCalls, 2);
});

test("handles a synchronous browser autoplay rejection", async () => {
  const audio = new MockAudio();
  let blocked = 0;
  audio.playBehavior = () => {
    throw Object.assign(new Error("Autoplay blocked"), { name: "NotAllowedError" });
  };
  const attempt = autoPlay(audio, { onBlocked: () => { blocked += 1; } });
  audio.makeReady();

  assert.equal(await attempt, false);
  assert.equal(blocked, 1);
});

test("reports audio loading failures without attempting playback", async () => {
  const audio = new MockAudio();
  let errors = 0;
  const attempt = autoPlay(audio, { onError: () => { errors += 1; } });
  audio.dispatchEvent(new Event("error"));

  assert.equal(await attempt, false);
  assert.equal(errors, 1);
  assert.equal(audio.playCalls, 0);
});

test("reports missing audio sources", async () => {
  let errors = 0;
  const attempt = autoPlay(new MockAudio(""), { onError: () => { errors += 1; } });

  assert.equal(await attempt, false);
  assert.equal(errors, 1);
});
