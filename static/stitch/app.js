(() => {
  "use strict";

  const routes = {
    home: "/",
    explore: "/explore",
    voice: "/voice",
    result: "/result",
    history: "/history",
    about: "/about"
  };
  const historyKey = "wildwhisper.explorations.v1";
  const pendingKey = "wildwhisper.stitch.pending.v1";
  const resultKey = "wildwhisper.stitch.result.v1";
  const maxImageBytes = 10 * 1024 * 1024;
  const screen = document.body.dataset.stitchScreen;
  const errorBox = document.querySelector("#ww-error");
  const loadingBox = document.querySelector("#ww-loading");
  const navLinks = document.querySelectorAll("[data-path]");
  let busy = false;
  let selectedFile = null;
  let selectedPhotoData = "";
  let previewObjectUrl = "";
  let countdownTimer = null;

  for (const link of navLinks) {
    const target = routes[link.dataset.path];
    if (target) link.href = target;
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
    errorBox.hidden = false;
    errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function clearError() {
    if (!errorBox) return;
    errorBox.textContent = "";
    errorBox.classList.add("hidden");
    errorBox.hidden = true;
  }

  function setLoading(visible, message = "Listening to the wild...") {
    if (!loadingBox) return;
    loadingBox.querySelector("span").textContent = message;
    loadingBox.classList.toggle("hidden", !visible);
    loadingBox.hidden = !visible;
  }

  async function readResponse(response) {
    let body;
    try {
      body = await response.json();
    } catch {
      throw new Error("WildWhisper could not read the local service response. Please try again.");
    }
    if (!response.ok) throw new Error(body.error || "WildWhisper could not complete that request.");
    return body;
  }

  function setStatus(online) {
    document.querySelectorAll("header .font-label-sm").forEach(label => {
      const text = label.textContent.trim();
      if (text !== "Local AI Online" && text !== "Online" && text !== "Checking Local AI") return;
      label.textContent = online ? "Local AI Online" : "Local AI Offline";
      const badge = label.parentElement;
      if (badge) badge.classList.toggle("ww-status-offline", !online);
    });
  }

  document.querySelectorAll("header .font-label-sm").forEach(label => {
    if (label.textContent.trim() === "Local AI Online" || label.textContent.trim() === "Online") {
      label.textContent = "Checking Local AI";
    }
  });

  async function checkStatus() {
    try {
      const response = await fetch("/api/status", {
        headers: { Accept: "application/json" }
      });
      const body = await readResponse(response);
      setStatus(body.online === true);
      const modelStatus = document.querySelector("#ollama-model-status");
      if (modelStatus) modelStatus.textContent = body.online ? "Model available" : "Model unavailable";
      return body.online === true;
    } catch {
      setStatus(false);
      const modelStatus = document.querySelector("#ollama-model-status");
      if (modelStatus) modelStatus.textContent = "Model unavailable";
      return false;
    }
  }

  function loadHistory() {
    try {
      const rows = JSON.parse(localStorage.getItem(historyKey) || "[]");
      if (!Array.isArray(rows)) return [];
      return rows.filter(row =>
        row &&
        typeof row.question === "string" &&
        typeof row.answer === "string" &&
        typeof row.time === "string"
      ).slice(0, 6);
    } catch {
      return [];
    }
  }

  function storeHistory(rows) {
    try {
      localStorage.setItem(historyKey, JSON.stringify(rows.slice(0, 6)));
    } catch {
      showError("This browser could not save the exploration. You can still read the answer.");
    }
  }

  function categoryFor(row) {
    const text = `${row.question} ${row.answer}`.toLowerCase();
    if (/\b(mushroom|fungus|fungi|mold|mould)\b/.test(text)) return "fungi";
    if (/\b(plant|tree|flower|leaf|leaves|fern|moss|shrub)\b/.test(text)) return "flora";
    return "other";
  }

  async function imageDataUrl(file, maxSide = 1280) {
    const objectUrl = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = objectUrl;
      await image.decode();
      const scale = Math.min(maxSide / image.width, maxSide / image.height, 1);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("The selected image could not be prepared.");
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/jpeg", 0.82);
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  }

  function dataUrlFile(dataUrl) {
    const [header, encoded] = dataUrl.split(",", 2);
    const mime = /data:([^;]+)/.exec(header)?.[1] || "image/jpeg";
    const bytes = atob(encoded);
    const buffer = new Uint8Array(bytes.length);
    for (let index = 0; index < bytes.length; index += 1) {
      buffer[index] = bytes.charCodeAt(index);
    }
    return new File([buffer], "wildwhisper-photo.jpg", { type: mime });
  }

  function readPending() {
    try {
      const pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
      return pending && typeof pending === "object" ? pending : {};
    } catch {
      return {};
    }
  }

  function storePending(value) {
    try {
      sessionStorage.setItem(pendingKey, JSON.stringify(value));
      return true;
    } catch {
      showError("The photo could not be kept for voice mode. Choose a smaller photo and try again.");
      return false;
    }
  }

  function displayHistory() {
    const section = document.querySelector("#exploration-entries");
    const empty = document.querySelector("#filter-empty-state");
    if (!section || !empty) return;
    const entries = loadHistory();
    const filters = document.querySelectorAll(".filter-chip");
    const counts = { all: entries.length, flora: 0, fungi: 0, acoustic: 0, other: 0 };
    entries.forEach(row => {
      const category = categoryFor(row);
      counts[category] += 1;
    });

    filters.forEach(button => {
      const count = button.querySelector("span:last-child");
      if (count) count.textContent = String(counts[button.dataset.filter] || 0);
    });
    const total = document.querySelector("#history-total-count");
    if (total) total.textContent = String(entries.length);

    const activeFilter = document.querySelector(".filter-chip.active")?.dataset.filter || "all";
    const visible = entries.filter(row =>
      activeFilter === "all" || categoryFor(row) === activeFilter
    );
    section.replaceChildren();
    visible.forEach(row => section.append(createHistoryCard(row)));
    empty.classList.toggle("hidden", visible.length > 0);
    empty.classList.toggle("flex", visible.length === 0);
    const emptyTitle = document.querySelector("#history-empty-title");
    const emptyDescription = empty.querySelector("p");
    if (entries.length === 0) {
      if (emptyTitle) emptyTitle.textContent = "No explorations yet";
      if (emptyDescription) emptyDescription.textContent = "Your recent questions and answers will appear here on this device.";
    } else {
      if (emptyTitle) emptyTitle.textContent = "No explorations here yet";
      if (emptyDescription) emptyDescription.textContent = "No saved explorations match this filter.";
    }
  }

  function createHistoryCard(row) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ww-history-entry group relative flex flex-col bg-surface-container-low rounded-xl overflow-hidden shadow-md transition-all active:bg-surface-container";
    const layout = document.createElement("div");
    layout.className = "p-space-md flex flex-col gap-space-sm";
    const content = document.createElement("div");
    content.className = "flex items-start gap-space-md";
    const thumbnail = document.createElement("div");
    thumbnail.className = "relative w-24 h-24 shrink-0 rounded-lg overflow-hidden bg-surface-container-highest shadow-inner";
    if (row.image) {
      const image = document.createElement("img");
      image.className = "w-full h-full object-cover";
      image.alt = "Previously explored photo";
      image.src = row.image;
      thumbnail.append(image);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "material-symbols-outlined w-full h-full flex items-center justify-center text-3xl text-primary";
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.textContent = "nature";
      thumbnail.append(placeholder);
    }
    const words = document.createElement("div");
    words.className = "flex-1 min-w-0 flex flex-col justify-between self-stretch";
    const heading = document.createElement("h2");
    heading.className = "font-headline-sm text-headline-sm text-on-surface tracking-tight leading-snug line-clamp-2";
    heading.textContent = row.question;
    const answer = document.createElement("p");
    answer.className = "font-body-sm text-body-sm text-on-surface-variant line-clamp-2 mt-1";
    answer.textContent = row.answer;
    const category = document.createElement("span");
    category.className = "font-label-sm text-label-sm text-primary tracking-wider uppercase";
    category.textContent = row.safety ? "Caution" : "Field observation";
    words.append(category, heading, answer);
    content.append(thumbnail, words);
    layout.append(content);
    const metadata = document.createElement("div");
    metadata.className = "flex items-center justify-between pt-2 mt-1 bg-surface-container-high/40 -mx-space-md -mb-space-md px-space-md py-2";
    const time = document.createElement("time");
    time.className = "font-label-sm text-label-sm text-on-surface-variant truncate";
    const date = new Date(row.time);
    time.textContent = Number.isNaN(date.valueOf())
      ? "Recent exploration"
      : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
    metadata.append(time);
    button.append(layout, metadata);
    button.addEventListener("click", () => {
      try {
        sessionStorage.setItem(resultKey, JSON.stringify({ ...row, audio: null }));
        window.location.assign(routes.result);
      } catch {
        showError("This saved exploration could not be opened.");
      }
    });
    return button;
  }

  function splitAnswer(answer) {
    const boundary = answer.search(/[.!?](?:\s|$)/);
    if (boundary < 0) return [answer, ""];
    const end = boundary + 1;
    return [answer.slice(0, end), answer.slice(end).trim()];
  }

  function renderResult() {
    let data;
    try {
      data = JSON.parse(sessionStorage.getItem(resultKey) || "null");
    } catch {
      data = null;
    }
    if (!data || typeof data.answer !== "string" || typeof data.question !== "string") {
      showError("There is no recent answer to display. Start with a photo and question.");
      return;
    }
    document.querySelector("#result-question").textContent = data.question;
    const [title, detail] = splitAnswer(data.answer);
    document.querySelector("#result-answer-title").textContent = title;
    document.querySelector("#result-answer-body").textContent = detail;
    document.querySelector("#result-answer-body").hidden = !detail;
    const photo = document.querySelector("#result-photo");
    if (photo && data.image) {
      photo.src = data.image;
      photo.classList.remove("hidden");
    }
    const safety = document.querySelector("#result-safety-warning");
    if (safety) safety.hidden = data.safety !== true;

    const audio = document.querySelector("#response-audio");
    const button = document.querySelector("#audio-toggle-btn");
    const audioAvailability = document.querySelector("#audio-availability");
    if (audio && button) {
      if (typeof data.audio === "string" && data.audio.length) {
        audio.src = `data:audio/mpeg;base64,${data.audio}`;
        button.disabled = false;
        button.setAttribute("aria-label", "Play response audio");
        if (audioAvailability) audioAvailability.textContent = "Audio response available";
      } else {
        button.disabled = true;
        button.setAttribute("aria-label", "Response audio unavailable");
        if (audioAvailability) audioAvailability.textContent = "Audio response unavailable";
      }
    }
    setupAudio();
  }

  async function saveResult(data, imageData) {
    const entries = loadHistory();
    const photo = imageData || selectedPhotoData;
    let thumbnail = photo;
    if (photo) {
      try {
        thumbnail = await imageDataUrl(dataUrlFile(photo), 480);
      } catch {
        thumbnail = "";
      }
    }
    const row = {
      question: data.question,
      answer: data.answer,
      safety: data.safety_warning === true,
      image: thumbnail,
      time: new Date().toISOString()
    };
    entries.unshift(row);
    storeHistory(entries);
    try {
      sessionStorage.setItem(resultKey, JSON.stringify({
        ...row,
        audio: typeof data.audio === "string" ? data.audio : null
      }));
      window.location.assign(routes.result);
    } catch {
      showError("The answer was received, but this browser could not open the result screen.");
    }
  }

  async function ask(question, file, photoData = "") {
    const cleanQuestion = question.trim();
    if (!file) {
      showError("Choose a photo before asking WildWhisper.");
      return;
    }
    if (!cleanQuestion) {
      showError("Enter a question, or ask using your voice.");
      document.querySelector("#question-input")?.focus();
      return;
    }
    if (busy) return;
    if (photoData && !storePending({ image: photoData, question: cleanQuestion })) return;
    busy = true;
    clearError();
    setLoading(true, "Listening to the wild...");
    document.querySelector("#submit-cta")?.setAttribute("disabled", "");
    document.querySelector("#voice-cta")?.setAttribute("disabled", "");
    try {
      const body = new FormData();
      body.set("photo", file, file.name);
      body.set("question", cleanQuestion);
      const response = await fetch("/api/ask", { method: "POST", body });
      const data = await readResponse(response);
      if (typeof data.answer !== "string") throw new Error("The local model returned an invalid answer.");
      await saveResult(data, photoData);
    } catch (error) {
      showError(error instanceof Error ? error.message : "WildWhisper could not complete that request.");
    } finally {
      busy = false;
      setLoading(false);
      document.querySelector("#submit-cta")?.removeAttribute("disabled");
      document.querySelector("#voice-cta")?.removeAttribute("disabled");
    }
  }

  function configureExplore() {
    const photoInput = document.querySelector("#photo-input");
    const preview = document.querySelector("#explore-photo-preview");
    const question = document.querySelector("#question-input");
    const clear = document.querySelector("#clear-btn");
    const removePhoto = document.querySelector("#remove-photo-btn");
    const filename = document.querySelector("#photo-filename");
    const pending = readPending();
    if (!photoInput || !preview || !question) return;
    if (typeof pending.question === "string") question.value = pending.question;
    if (clear) clear.hidden = question.value.length === 0;
    if (typeof pending.image === "string" && pending.image) {
      selectedPhotoData = pending.image;
      preview.src = pending.image;
      preview.classList.remove("hidden");
      document.querySelector("#photo-empty-prompt")?.classList.add("hidden");
      selectedFile = dataUrlFile(pending.image);
      if (removePhoto) removePhoto.classList.remove("hidden");
      if (filename) filename.textContent = "Selected photo";
    }
    if (new URLSearchParams(window.location.search).has("voice")) {
      showError("Your spoken question is ready. Choose a photo, then ask WildWhisper.");
    }
    const choose = () => photoInput.click();
    document.querySelector("#browse-photo-btn")?.addEventListener("click", choose);
    document.querySelector("#camera-photo-btn")?.addEventListener("click", choose);
    preview.addEventListener("click", choose);
    const selectPhoto = async file => {
      if (!file) return;
      clearError();
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > maxImageBytes) {
        showError("Choose a JPG, PNG, or WebP image under 10 MB.");
        return;
      }
      try {
        const photoData = await imageDataUrl(file);
        selectedFile = file;
        selectedPhotoData = photoData;
        if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
        previewObjectUrl = URL.createObjectURL(file);
        preview.src = previewObjectUrl;
        preview.classList.remove("hidden");
        document.querySelector("#photo-empty-prompt")?.classList.add("hidden");
        if (filename) filename.textContent = file.name;
        if (removePhoto) removePhoto.classList.remove("hidden");
        storePending({ image: photoData, question: question.value });
      } catch (error) {
        showError(error instanceof Error ? error.message : "The selected image could not be opened.");
      }
    };
    photoInput.addEventListener("change", () => selectPhoto(photoInput.files?.[0]));
    preview.parentElement?.addEventListener("dragover", event => {
      event.preventDefault();
      preview.parentElement.classList.add("ww-drop-active");
    });
    preview.parentElement?.addEventListener("dragleave", event => {
      if (!preview.parentElement.contains(event.relatedTarget)) {
        preview.parentElement.classList.remove("ww-drop-active");
      }
    });
    preview.parentElement?.addEventListener("drop", event => {
      event.preventDefault();
      preview.parentElement.classList.remove("ww-drop-active");
      selectPhoto(event.dataTransfer?.files?.[0]);
    });
    removePhoto?.addEventListener("click", () => {
      selectedFile = null;
      selectedPhotoData = "";
      photoInput.value = "";
      if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
      previewObjectUrl = "";
      preview.removeAttribute("src");
      preview.classList.add("hidden");
      document.querySelector("#photo-empty-prompt")?.classList.remove("hidden");
      if (filename) filename.textContent = "Choose or drop a JPG, PNG, or WebP photo";
      removePhoto.classList.add("hidden");
      storePending({ image: "", question: question.value });
      clearError();
    });
    clear?.addEventListener("click", () => {
      question.value = "";
      clear.hidden = true;
      question.focus();
    });
    question.addEventListener("input", () => {
      if (clear) clear.hidden = question.value.length === 0;
      if (selectedPhotoData) storePending({ image: selectedPhotoData, question: question.value });
    });
    document.querySelectorAll(".suggestion-chip").forEach(chip => {
      chip.addEventListener("click", () => {
        const text = chip.querySelector("span:last-child")?.textContent.trim() || chip.textContent.trim();
        question.value = text;
        question.dispatchEvent(new Event("input", { bubbles: true }));
        question.focus();
      });
    });
    document.querySelector("#submit-cta")?.addEventListener("click", () => {
      ask(question.value, selectedFile, selectedPhotoData);
    });
    document.querySelector("#voice-cta")?.addEventListener("click", async () => {
      try {
        const image = selectedFile ? selectedPhotoData || await imageDataUrl(selectedFile) : "";
        if (!storePending({ image, question: question.value })) return;
        window.location.assign(routes.voice);
      } catch (error) {
        showError(error instanceof Error ? error.message : "The photo could not be prepared for voice mode.");
      }
    });
  }

  async function finishVoice() {
    if (busy) return;
    busy = true;
    clearError();
    const mic = document.querySelector("#micOrb");
    const finish = document.querySelector("#finishButton");
    const transcript = document.querySelector("#liveTranscript");
    const questionInput = document.querySelector("#voice-question-input");
    const timer = document.querySelector("#recordingTimer");
    const voiceStatus = document.querySelector("#voice-status");
    if (mic) mic.disabled = true;
    if (finish) finish.disabled = true;
    if (questionInput) questionInput.disabled = true;
    if (voiceStatus) voiceStatus.textContent = "Recording...";
    if (mic) mic.setAttribute("aria-label", "Recording your question");
    setLoading(true, "Recording for up to 8 seconds. Speak clearly near the microphone.");
    let remaining = 8;
    if (timer) timer.textContent = "0:08";
    window.clearInterval(countdownTimer);
    countdownTimer = window.setInterval(() => {
      remaining = Math.max(0, remaining - 1);
      if (timer) timer.textContent = `0:0${remaining}`;
    }, 1000);
    try {
      const response = await fetch("/api/listen", { method: "POST" });
      const data = await readResponse(response);
      if (typeof data.question !== "string" || !data.question.trim()) {
        throw new Error("No speech was recognized. Try again in a quieter place.");
      }
      const recognizedQuestion = data.question.trim();
      if (questionInput) {
        questionInput.value = recognizedQuestion;
        questionInput.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (transcript) transcript.textContent = `“${recognizedQuestion}”`;
      if (voiceStatus) {
        voiceStatus.textContent = data.low_confidence === true
          ? "Review uncertain words or retry"
          : "Review or record again";
      }
      if (mic) mic.setAttribute("aria-label", "Record your question again");
    } catch (error) {
      if (voiceStatus) voiceStatus.textContent = "Ready to retry";
      showError(error instanceof Error ? error.message : "Voice input could not be completed.");
    } finally {
      window.clearInterval(countdownTimer);
      setLoading(false);
      busy = false;
      if (voiceStatus && voiceStatus.textContent === "Recording...") voiceStatus.textContent = "Ready to listen";
      if (mic) mic.disabled = false;
      if (questionInput) questionInput.disabled = false;
      if (finish && questionInput) finish.disabled = !questionInput.value.trim();
    }
  }

  async function completeVoice(question) {
    const cleanQuestion = typeof question === "string" ? question.trim() : "";
    if (!cleanQuestion) {
      showError("No question is ready. Use the microphone or enter a question first.");
      document.querySelector("#voice-question-input")?.focus();
      return;
    }
    const pending = readPending();
    if (!pending.image) {
      if (busy) return;
      busy = true;
      if (!storePending({ question: cleanQuestion, image: "" })) {
        busy = false;
        return;
      }
      window.location.assign(`${routes.explore}?voice=1`);
      return;
    }
    try {
      const file = dataUrlFile(pending.image);
      await ask(cleanQuestion, file, pending.image);
    } catch (error) {
      showError(error instanceof Error ? error.message : "The saved photo could not be opened.");
    }
  }

  function configureVoice() {
    const pending = readPending();
    const transcript = document.querySelector("#liveTranscript");
    const questionInput = document.querySelector("#voice-question-input");
    const finish = document.querySelector("#finishButton");
    if (transcript && pending.question) transcript.textContent = `“${pending.question}”`;
    if (questionInput && typeof pending.question === "string") {
      questionInput.value = pending.question;
    }
    if (finish && questionInput) finish.disabled = !questionInput.value.trim();
    questionInput?.addEventListener("input", () => {
      if (finish) finish.disabled = !questionInput.value.trim() || busy;
      if (transcript) {
        transcript.textContent = questionInput.value.trim()
          ? `“${questionInput.value.trim()}”`
          : "“Press the microphone and speak your question...”";
      }
    });
    document.querySelector("#micOrb")?.addEventListener("click", () => finishVoice());
    finish?.addEventListener("click", () => {
      void completeVoice(questionInput?.value || "");
    });
    document.querySelectorAll(".prompt-chip").forEach(button => {
      button.addEventListener("click", () => {
        const prompt = button.querySelector("span.truncate")?.textContent.replace(/[“”"]/g, "").trim();
        if (!prompt) return;
        if (questionInput) {
          questionInput.value = prompt;
          questionInput.dispatchEvent(new Event("input", { bubbles: true }));
        } else if (transcript) {
          transcript.textContent = `“${prompt}”`;
        }
      });
    });
    document.querySelectorAll("button").forEach(button => {
      const label = button.textContent.trim().toLowerCase();
      if (label.includes("keyboard mode")) {
        button.addEventListener("click", () => {
          const text = questionInput?.value.trim() || "";
          if (!text) {
            showError("Use the microphone or enter a question before switching to keyboard mode.");
            questionInput?.focus();
            return;
          }
          if (!storePending({ ...pending, question: text })) return;
          window.location.assign(`${routes.explore}?voice=1`);
        });
      }
      if (label.includes("cancel")) {
        button.addEventListener("click", () => window.location.assign(routes.explore));
      }
    });
    const params = new URLSearchParams(window.location.search);
    if (params.has("voice") && questionInput) {
      questionInput.focus();
      showError("Your spoken question is ready. Add a photo, then ask WildWhisper.");
    }
  }

  function formatTime(value) {
    if (!Number.isFinite(value) || value < 0) return "0:00";
    const minutes = Math.floor(value / 60);
    const seconds = Math.floor(value % 60).toString().padStart(2, "0");
    return `${minutes}:${seconds}`;
  }

  function setupAudio() {
    const audio = document.querySelector("#response-audio");
    if (!audio) return;
    const toggle = document.querySelector("#audio-toggle-btn");
    const icon = document.querySelector("#audio-play-icon");
    const progress = document.querySelector("#audio-scrub-progress");
    const track = document.querySelector("#audio-scrub-track");
    const current = document.querySelector("#audio-current-time");
    const duration = document.querySelector("#audio-duration");
    const audioAvailability = document.querySelector("#audio-availability");
    const audioHelper = window.WildWhisperAudio;
    let autoPlaybackPending = Boolean(audio.src && audioHelper);
    if (toggle && autoPlaybackPending) toggle.disabled = true;
    toggle?.addEventListener("click", async () => {
      if (autoPlaybackPending) return;
      try {
        if (audio.paused) {
          await audio.play();
          clearError();
          if (audioAvailability) audioAvailability.textContent = "Playing response";
        } else {
          audio.pause();
          if (audioAvailability) audioAvailability.textContent = "Response audio paused";
        }
      } catch (error) {
        if (error instanceof Error && error.name === "NotAllowedError") {
          showError("Your browser blocked audio playback. Check the browser's sound permissions and try again.");
        } else {
          showError("Audio could not be played in this browser. Use the play button to retry.");
        }
      }
    });
    audio.addEventListener("play", () => {
      if (icon) icon.textContent = "pause";
    });
    audio.addEventListener("pause", () => {
      if (icon) icon.textContent = "volume_up";
    });
    audio.addEventListener("timeupdate", () => {
      if (current) current.textContent = formatTime(audio.currentTime);
      if (progress && Number.isFinite(audio.duration) && audio.duration > 0) {
        progress.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
      }
      if (track) {
        track.setAttribute("aria-valuemax", String(Number.isFinite(audio.duration) ? audio.duration : 0));
        track.setAttribute("aria-valuenow", String(audio.currentTime));
      }
    });
    audio.addEventListener("loadedmetadata", () => {
      if (duration) duration.textContent = formatTime(audio.duration);
    });
    audio.addEventListener("ended", () => {
      if (icon) icon.textContent = "volume_up";
    });
    audio.addEventListener("error", () => {
      if (toggle) {
        toggle.disabled = true;
        toggle.setAttribute("aria-label", "Response audio unavailable");
      }
      if (audioAvailability) audioAvailability.textContent = "Response audio could not be loaded";
      showError("The response audio could not be loaded.");
    });
    track?.addEventListener("click", event => {
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const bounds = track.getBoundingClientRect();
      audio.currentTime = ((event.clientX - bounds.left) / bounds.width) * audio.duration;
    });
    track?.addEventListener("keydown", event => {
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      if (event.key === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 5);
      else if (event.key === "ArrowRight") audio.currentTime = Math.min(audio.duration, audio.currentTime + 5);
      else return;
      event.preventDefault();
    });
    document.querySelectorAll(".speed-toggle").forEach(button => {
      button.addEventListener("click", () => {
        audio.playbackRate = Number(button.dataset.speed) || 1;
        document.querySelectorAll(".speed-toggle").forEach(item => {
          item.classList.toggle("bg-primary-container", item === button);
          item.classList.toggle("text-on-primary", item === button);
        });
      });
    });
    if (audio.src && audioHelper) {
      if (audioAvailability) audioAvailability.textContent = "Preparing response audio...";
      void audioHelper.autoPlay(audio, {
        onBlocked: () => {
          autoPlaybackPending = false;
          if (toggle) toggle.disabled = false;
          if (audioAvailability) audioAvailability.textContent = "Autoplay blocked. Tap Play to listen.";
          showError("Your browser blocked automatic audio playback. Tap Play Response to listen.");
        },
        onError: () => {
          autoPlaybackPending = false;
          if (toggle) {
            toggle.disabled = true;
            toggle.setAttribute("aria-label", "Response audio unavailable");
          }
          if (audioAvailability) audioAvailability.textContent = "Response audio could not be loaded";
          showError("The response audio could not be loaded. Please try asking again.");
        }
      }).then(played => {
        autoPlaybackPending = false;
        if (toggle && !audio.error) toggle.disabled = false;
        if (played && audioAvailability) audioAvailability.textContent = "Playing response";
      });
    }
  }

  function configureResult() {
    renderResult();
    document.querySelector("#result-back-button")?.addEventListener("click", () => {
      window.location.assign(routes.explore);
    });
    const zoomButton = document.querySelector("#zoom-specimen-btn");
    const resultPhoto = document.querySelector("#result-photo");
    zoomButton?.addEventListener("click", () => {
      const zoomed = resultPhoto?.classList.toggle("ww-zoomed") === true;
      zoomButton.querySelector("span").textContent = zoomed ? "zoom_out" : "zoom_in";
      zoomButton.setAttribute("aria-label", zoomed ? "Zoom out on image" : "Zoom in on image");
    });
    document.querySelector("#copy-btn")?.addEventListener("click", async () => {
      const data = JSON.parse(sessionStorage.getItem(resultKey) || "{}");
      try {
        await navigator.clipboard.writeText(data.answer || "");
      } catch {
        showError("The answer could not be copied. Select the text to copy it manually.");
      }
    });
    document.querySelector("#save-btn")?.addEventListener("click", () => {
      const data = JSON.parse(sessionStorage.getItem(resultKey) || "{}");
      if (!data.question || !data.answer) return;
      const entries = loadHistory();
      if (!entries.some(row => row.question === data.question && row.answer === data.answer)) {
        entries.unshift({ ...data, time: data.time || new Date().toISOString() });
        storeHistory(entries);
      }
      const label = document.querySelector("#save-label");
      if (label) label.textContent = "Saved";
    });
    document.querySelector("#share-btn")?.addEventListener("click", async () => {
      const data = JSON.parse(sessionStorage.getItem(resultKey) || "{}");
      try {
        if (navigator.share) await navigator.share({ title: "WildWhisper", text: `${data.question}\n\n${data.answer}` });
        else await navigator.clipboard.writeText(`${data.question}\n\n${data.answer}`);
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") return;
        showError("This browser could not share the answer.");
      }
    });
    document.querySelectorAll("button").forEach(button => {
      const label = button.textContent.trim().toLowerCase();
      if (label.includes("ask another question")) {
        button.addEventListener("click", () => window.location.assign(routes.explore));
      }
      if (label.includes("scan new specimen")) {
        button.addEventListener("click", () => {
          sessionStorage.removeItem(pendingKey);
          window.location.assign(routes.explore);
        });
      }
    });
  }

  function configureHistory() {
    displayHistory();
    document.querySelectorAll(".filter-chip").forEach(button => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".filter-chip").forEach(chip => {
          chip.classList.toggle("active", chip === button);
          chip.classList.toggle("bg-primary-container", chip === button);
          chip.classList.toggle("text-on-primary-container", chip === button);
        });
        displayHistory();
      });
    });
  }

  function configureAbout() {
    document.querySelector("#pingBtn")?.addEventListener("click", async () => {
      const online = await checkStatus();
      if (online) clearError();
      else showError("Ollama or the configured model is unavailable.");
    });
  }

  function enableUtilityFallback() {
    window.setTimeout(() => {
      const main = document.querySelector("main");
      if (main && getComputedStyle(main).display !== "flex") {
        document.documentElement.classList.add("utility-css-unavailable");
      }
    }, 2500);
  }

  enableUtilityFallback();
  checkStatus();
  window.setInterval(checkStatus, 20000);

  if (screen === "home") {
    document.querySelector("#voiceTriggerBtn")?.addEventListener("click", () => {
      storePending({ image: "", question: "" });
      window.location.assign(routes.voice);
    });
  } else if (screen === "explore") {
    configureExplore();
  } else if (screen === "voice") {
    configureVoice();
  } else if (screen === "result") {
    configureResult();
  } else if (screen === "history") {
    configureHistory();
  } else if (screen === "about") {
    configureAbout();
  }
})();
