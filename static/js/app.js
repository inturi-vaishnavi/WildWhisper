(() => {
  "use strict";

  const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
  const HISTORY_KEY = "wildwhisper.explorations.v1";
  const HISTORY_LIMIT = 6;
  const form = document.querySelector("#question-form");
  const photoInput = document.querySelector("#photo-input");
  const dropZone = document.querySelector("#drop-zone");
  const previewWrap = document.querySelector("#preview-wrap");
  const imagePreview = document.querySelector("#image-preview");
  const uploadPrompt = document.querySelector("#upload-prompt");
  const questionInput = document.querySelector("#question-input");
  const askButton = document.querySelector("#ask-button");
  const voiceButton = document.querySelector("#voice-button");
  const voiceState = document.querySelector("#voice-state");
  const voiceCountdown = document.querySelector("#voice-countdown");
  const errorMessage = document.querySelector("#error-message");
  const loadingState = document.querySelector("#loading-state");
  const loadingLabel = document.querySelector("#loading-label");
  const resultSection = document.querySelector("#result-section");
  const resultPhoto = document.querySelector("#result-photo");
  const resultQuestion = document.querySelector("#result-question");
  const resultAnswer = document.querySelector("#result-answer");
  const safetyWarning = document.querySelector("#safety-warning");
  const playButton = document.querySelector("#play-response");
  const copyButton = document.querySelector("#copy-response");
  const responseAudio = document.querySelector("#response-audio");
  const audioNote = document.querySelector("#audio-note");
  const historySection = document.querySelector("#history");
  const historyEmpty = document.querySelector("#history-empty");
  const historyGrid = document.querySelector("#history-grid");
  const toast = document.querySelector("#toast");
  const statusPill = document.querySelector("#service-status");
  const statusLabel = document.querySelector("#service-status-label");
  let selectedFile = null;
  let previewUrl = null;
  let lastAnswer = "";
  let toastTimeout = null;
  let voiceCountdownTimer = null;
  let busy = false;

  function showError(message) {
    errorMessage.textContent = message;
    errorMessage.hidden = false;
    errorMessage.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function clearError() {
    errorMessage.textContent = "";
    errorMessage.hidden = true;
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("is-visible");
    window.clearTimeout(toastTimeout);
    toastTimeout = window.setTimeout(() => toast.classList.remove("is-visible"), 2400);
  }

  function setBusy(nextBusy, label = "Listening to the wild...", listening = false) {
    busy = nextBusy;
    askButton.disabled = nextBusy;
    voiceButton.disabled = nextBusy;
    loadingLabel.textContent = label;
    loadingState.hidden = !nextBusy || listening;
    voiceState.hidden = !nextBusy || !listening;
  }

  function startVoiceCountdown() {
    let secondsLeft = 8;
    voiceCountdown.textContent = "0:08";
    window.clearInterval(voiceCountdownTimer);
    voiceCountdownTimer = window.setInterval(() => {
      secondsLeft = Math.max(0, secondsLeft - 1);
      voiceCountdown.textContent = `0:0${secondsLeft}`;
      if (secondsLeft === 0) window.clearInterval(voiceCountdownTimer);
    }, 1000);
  }

  function isImageFile(file) {
    return Boolean(
      file &&
      file.size <= MAX_IMAGE_BYTES &&
      ["image/jpeg", "image/png", "image/webp"].includes(file.type)
    );
  }

  function acceptFile(file) {
    clearError();
    if (!file || !isImageFile(file)) {
      showError("Choose a JPG, PNG, or WebP image under 10 MB.");
      return;
    }
    selectedFile = file;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    imagePreview.src = previewUrl;
    uploadPrompt.hidden = true;
    previewWrap.hidden = false;
    resultSection.hidden = true;
  }

  function removeImage() {
    selectedFile = null;
    photoInput.value = "";
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    imagePreview.removeAttribute("src");
    previewWrap.hidden = true;
    uploadPrompt.hidden = false;
    resultSection.hidden = true;
  }

  function setStatus(online) {
    statusPill.dataset.state = online ? "online" : "offline";
    statusLabel.textContent = online ? "Local AI Online" : "Local AI Offline";
  }

  async function checkStatus() {
    try {
      const response = await fetch("/api/status", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("status unavailable");
      const data = await response.json();
      setStatus(data.online === true);
    } catch {
      setStatus(false);
    }
  }

  async function readResponse(response) {
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("WildWhisper could not read the server response. Please try again.");
    }
    if (!response.ok) throw new Error(data.error || "WildWhisper could not complete that request.");
    return data;
  }

  function makeAudioSource(encodedAudio) {
    if (!encodedAudio) return "";
    return `data:audio/mpeg;base64,${encodedAudio}`;
  }

  function renderResult(data) {
    lastAnswer = data.answer;
    resultPhoto.src = previewUrl;
    resultQuestion.textContent = data.question;
    resultAnswer.textContent = data.answer;
    safetyWarning.hidden = data.safety_warning !== true;
    responseAudio.pause();
    responseAudio.removeAttribute("src");
    const audioSource = makeAudioSource(data.audio);
    if (audioSource) {
      responseAudio.src = audioSource;
      playButton.disabled = false;
      playButton.querySelector("span").textContent = "Play response";
      audioNote.textContent = "";
    } else {
      playButton.disabled = true;
      playButton.querySelector("span").textContent = "Audio unavailable";
      audioNote.textContent = data.local_spoken
        ? "ElevenLabs was unavailable; the existing local speech fallback played on this computer."
        : "Speech playback was unavailable. Your answer is available as text.";
    }
    resultSection.hidden = false;
    resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
    saveExploration(data);
  }

  async function ask(question) {
    clearError();
    if (!selectedFile) {
      showError("Choose a photo before asking WildWhisper.");
      return;
    }
    if (!question.trim()) {
      showError("Enter a question, or ask using your voice.");
      questionInput.focus();
      return;
    }
    if (busy) return;
    setBusy(true);
    resultSection.hidden = true;
    loadingState.scrollIntoView({ behavior: "smooth", block: "center" });
    try {
      const body = new FormData();
      body.set("photo", selectedFile, selectedFile.name);
      body.set("question", question.trim());
      const response = await fetch("/api/ask", { method: "POST", body });
      const data = await readResponse(response);
      renderResult(data);
    } catch (error) {
      showError(error instanceof Error ? error.message : "WildWhisper could not complete that request.");
    } finally {
      setBusy(false);
    }
  }

  async function askWithVoice() {
    clearError();
    if (!selectedFile) {
      showError("Choose a photo before asking WildWhisper.");
      return;
    }
    if (busy) return;
    setBusy(true, "Listening for up to 8 seconds...", true);
    startVoiceCountdown();
    voiceState.scrollIntoView({ behavior: "smooth", block: "center" });
    try {
      const response = await fetch("/api/listen", { method: "POST" });
      const data = await readResponse(response);
      window.clearInterval(voiceCountdownTimer);
      questionInput.value = data.question;
      setBusy(true, "Question heard. Looking at your photo...");
      loadingLabel.textContent = "Question heard. Looking at your photo...";
      const body = new FormData();
      body.set("photo", selectedFile, selectedFile.name);
      body.set("question", data.question);
      const answerResponse = await fetch("/api/ask", { method: "POST", body });
      const answerData = await readResponse(answerResponse);
      renderResult(answerData);
    } catch (error) {
      showError(error instanceof Error ? error.message : "Voice input could not be completed.");
    } finally {
      window.clearInterval(voiceCountdownTimer);
      setBusy(false);
    }
  }

  function loadHistory() {
    try {
      const items = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(items) ? items.filter(item =>
        item && typeof item.question === "string" && typeof item.answer === "string"
      ).slice(0, HISTORY_LIMIT) : [];
    } catch {
      return [];
    }
  }

  function persistHistory(items) {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, HISTORY_LIMIT)));
    } catch {
      showToast("This browser could not save the recent exploration.");
    }
  }

  function thumbnailFor(file) {
    return new Promise(resolve => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => {
        const scale = Math.min(480 / image.width, 300 / image.height, 1);
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        const context = canvas.getContext("2d");
        if (!context) {
          URL.revokeObjectURL(url);
          resolve("");
          return;
        }
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const thumbnail = canvas.toDataURL("image/jpeg", 0.72);
        URL.revokeObjectURL(url);
        resolve(thumbnail);
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        resolve("");
      };
      image.src = url;
    });
  }

  async function saveExploration(data) {
    const items = loadHistory();
    const thumbnail = selectedFile ? await thumbnailFor(selectedFile) : "";
    items.unshift({
      question: data.question,
      answer: data.answer,
      image: thumbnail,
      time: new Date().toISOString()
    });
    persistHistory(items);
    renderHistory();
  }

  function renderHistory() {
    const items = loadHistory();
    historyGrid.replaceChildren();
    historySection.hidden = false;
    historyEmpty.hidden = items.length > 0;
    for (const item of items) {
      const card = document.createElement("button");
      card.className = "history-card";
      card.type = "button";
      const image = item.image ? document.createElement("img") : document.createElement("span");
      if (item.image) {
        image.alt = "Previously explored photo";
        image.src = item.image;
      } else {
        image.className = "history-image-unavailable";
        image.textContent = "Photo preview unavailable";
      }
      const question = document.createElement("strong");
      question.textContent = item.question;
      const answer = document.createElement("p");
      answer.textContent = item.answer;
      const time = document.createElement("time");
      const date = new Date(item.time);
      time.dateTime = Number.isNaN(date.valueOf()) ? "" : date.toISOString();
      time.textContent = Number.isNaN(date.valueOf())
        ? "Recent exploration"
        : new Intl.DateTimeFormat(undefined, {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit"
          }).format(date);
      card.append(image, question, answer, time);
      card.addEventListener("click", () => restoreExploration(item));
      historyGrid.append(card);
    }
  }

  async function restoreExploration(item) {
    questionInput.value = item.question;
    if (item.image) {
      try {
        const response = await fetch(item.image);
        const blob = await response.blob();
        const file = new File([blob], "wildwhisper-history.jpg", { type: "image/jpeg" });
        acceptFile(file);
      } catch {
        showError("That saved photo could not be opened. Choose the original image again.");
        return;
      }
    } else {
      showError("This saved exploration has no photo preview. Choose the original image again.");
      return;
    }
    document.querySelector("#explore").scrollIntoView({ behavior: "smooth" });
  }

  document.querySelector("#browse-button").addEventListener("click", () => photoInput.click());
  dropZone.addEventListener("click", event => {
    if (!event.target.closest("button")) photoInput.click();
  });
  dropZone.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      photoInput.click();
    }
  });
  photoInput.addEventListener("change", () => {
    if (photoInput.files && photoInput.files[0]) acceptFile(photoInput.files[0]);
  });
  document.querySelector("#remove-image").addEventListener("click", event => {
    event.stopPropagation();
    removeImage();
  });
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, event => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, event => {
      event.preventDefault();
      dropZone.classList.remove("is-dragging");
    });
  }
  dropZone.addEventListener("drop", event => {
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) acceptFile(file);
  });
  document.querySelectorAll(".suggestion-chip").forEach(button => {
    button.addEventListener("click", () => {
      questionInput.value = button.textContent.trim();
      questionInput.focus();
    });
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    ask(questionInput.value);
  });
  voiceButton.addEventListener("click", askWithVoice);
  document.querySelector("#ask-another").addEventListener("click", () => {
    resultSection.hidden = true;
    questionInput.value = "";
    questionInput.focus();
    document.querySelector("#explore").scrollIntoView({ behavior: "smooth" });
  });
  playButton.addEventListener("click", async () => {
    if (!responseAudio.src) return;
    try {
      if (responseAudio.paused) await responseAudio.play();
      else responseAudio.pause();
    } catch {
      audioNote.textContent = "Audio could not be played in this browser.";
    }
  });
  responseAudio.addEventListener("ended", () => {
    playButton.querySelector("span").textContent = "Play response";
  });
  responseAudio.addEventListener("play", () => {
    playButton.querySelector("span").textContent = "Pause response";
  });
  responseAudio.addEventListener("pause", () => {
    playButton.querySelector("span").textContent = "Play response";
  });
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(lastAnswer);
      showToast("Answer copied.");
    } catch {
      showError("This browser could not copy the answer. Select the text to copy it.");
    }
  });

  renderHistory();
  checkStatus();
  window.setInterval(checkStatus, 30000);
})();
