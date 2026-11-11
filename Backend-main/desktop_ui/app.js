/*
  Веб-клиент PixelForge (desktop_ui).
  - работает с API по same-origin: страницу и API отдаёт один и тот же
    FastAPI-сервер (python server.py), никакого ?api=... и второго сервера;
  - не содержит алгоритмов конвертации/валидации;
  - логика экрана соответствует ТЗ п.5.3 (валидация, заморозка, прогресс, ошибки).
*/
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const API_BASE = ""; // same-origin: сайт и API на одном адресе
  const HINT =
    "Перетащите сюда папку или архив с изображениями (.zip, .7z) " +
    "или нажмите, чтобы выбрать файл";
  const IMAGE_RE = /\.(png|jpe?g|bmp|webp|gif|ico|tiff?|avif)$/i;
  const ARCHIVE_RE = /\.(zip|7z)$/i;
  const RAR_RE = /\.rar$/i;

  const el = {
    dropzone: $("dropzone"),
    sourceInfo: $("sourceInfo"),
    downloadBtn: $("downloadBtn"),
    archiveBtn: $("archiveBtn"),
    fileInput: $("fileInput"),
    dirInput: $("dirInput"),
    settingsPanel: $("settingsPanel"),
    format: $("format"),
    interp: $("interp"),
    quality: $("quality"),
    qualityValue: $("qualityValue"),
    resizeEnabled: $("resizeEnabled"),
    dims: $("dims"),
    width: $("width"),
    height: $("height"),
    keepAspect: $("keepAspect"),
    log: $("log"),
    progressTrack: $("progressTrack"),
    progressFill: $("progressFill"),
    progressLabel: $("progressLabel"),
    statusLabel: $("statusLabel"),
    outLabel: $("outLabel"),
    startBtn: $("startBtn"),
    abortBtn: $("abortBtn"),
    modal: $("modal"),
    modalTitle: $("modalTitle"),
    modalText: $("modalText"),
    modalOk: $("modalOk"),
  };

  const state = {
    config: {},
    source: null,
    job: null,
    localBusy: false,
    offline: false,
    configLoaded: false,
    renderedStatus: null,
  };

  let persistTimer = null;
  let logFirstLoad = true;
  let audioCtx = null;

  // ------------------------------------------------------------------ API
  async function api(path, options = {}) {
    const opts = { method: options.method || "GET", headers: {} };
    if (options.form) {
      opts.body = options.form;
    } else if (options.body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(options.body);
    }
    const res = await fetch(`${API_BASE}/api${path}`, opts);
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!res.ok) {
      const detail = data && typeof data === "object" ? data.detail : data;
      throw new Error(detail || `HTTP ${res.status}`);
    }
    return data;
  }

  const bridge = () =>
    window.pywebview && window.pywebview.api ? window.pywebview.api : null;

  // ------------------------------------------------------------------ UI helpers
  function pluralFiles(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return "файл";
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "файла";
    return "файлов";
  }

  function updateDimsEnabled() {
    const enabled = el.resizeEnabled.checked && !el.resizeEnabled.disabled;
    el.dims.classList.toggle("disabled", !enabled);
    el.width.disabled = !enabled;
    el.height.disabled = !enabled;
    el.keepAspect.disabled = !enabled;
  }

  function showError(message) {
    const text = String(message || "Неизвестная ошибка");
    el.modalTitle.textContent = /валидац/i.test(text)
      ? "Ошибка валидации"
      : "Ошибка";
    el.modalText.textContent = text;
    el.modal.hidden = false;
    el.modalOk.focus();
  }

  function closeModal() {
    el.modal.hidden = true;
  }

  function flashDropzone() {
    el.dropzone.classList.add("error");
    window.setTimeout(() => el.dropzone.classList.remove("error"), 800);
  }

  function primeAudio() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = audioCtx || new Ctx();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch {
      /* звук необязателен */
    }
  }

  function beep() {
    try {
      primeAudio();
      if (!audioCtx) return;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      const t = audioCtx.currentTime;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.04, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t);
      osc.stop(t + 0.38);
    } catch {
      /* звук необязателен */
    }
  }

  // ------------------------------------------------------------------ настройки
  function collectSettings() {
    return {
      output_format: el.format.value,
      quality: Number(el.quality.value),
      interpolation: el.interp.value,
      resize_enabled: el.resizeEnabled.checked,
      width: el.width.value ? Number(el.width.value) : null,
      height: el.height.value ? Number(el.height.value) : null,
      keep_aspect: el.keepAspect.checked,
      output_dir: state.config.output_dir || "",
    };
  }

  function applyConfig(cfg) {
    if (!cfg) return;
    state.config = { ...cfg };
    el.format.value = cfg.output_format || "WebP";
    el.interp.value = cfg.interpolation || "Lanczos";
    el.quality.value = String(cfg.quality ?? 80);
    el.qualityValue.textContent = `${el.quality.value}%`;
    el.resizeEnabled.checked = Boolean(cfg.resize_enabled);
    el.width.value = String(cfg.width ?? 1920);
    el.height.value = String(cfg.height ?? 1080);
    el.keepAspect.checked = cfg.keep_aspect !== false;
    updateDimsEnabled();
    renderOut();
    state.configLoaded = true;
  }

  function schedulePersist() {
    if (!state.configLoaded || state.localBusy) return;
    window.clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => {
      persist().catch((err) => showError(err.message));
    }, 300);
  }

  async function persist() {
    const cfg = await api("/config", { method: "PUT", body: collectSettings() });
    state.config = { ...cfg };
    renderOut();
  }

  function renderOut() {
    const job = state.job || {};
    const chosen = (state.config.output_dir || "").trim();
    const actual = job.output_dir ? String(job.output_dir) : "";
    const value = chosen || actual;
    el.outLabel.textContent =
      `Выходная папка: ${value || "— не выбрана (рядом с источником / Converted)"}`;
  }

  // ------------------------------------------------------------------ источники
  function renderSource() {
    const src = state.source;
    const ready = Boolean(src && src.ready);
    el.sourceInfo.textContent = ready
      ? src.label || `${src.name} — ${src.count} ${pluralFiles(src.count)} готово к конвертации`
      : HINT;
    el.sourceInfo.classList.toggle("ready", ready);
    renderJob();
  }

  async function syncData(applyCfg) {
    const [cfg, source, job] = await Promise.all([
      api("/config"),
      api("/source"),
      api("/job"),
    ]);
    if (applyCfg) applyConfig(cfg);
    else state.config = { ...cfg, ...state.config };
    state.source = source;
    state.job = job;
    renderSource();
    renderJob();
    hydrateLog(job.logs || []);
  }

  async function refresh() {
    const [source, job] = await Promise.all([api("/source"), api("/job")]);
    state.source = source;
    state.job = job;
    renderSource();
    renderJob();
    return job;
  }

  async function loadLocalPath(path) {
    if (state.localBusy) return;
    state.localBusy = true;
    renderJob();
    try {
      state.source = await api("/source", { method: "POST", body: { path } });
      await refresh();
    } catch (err) {
      showError(err.message);
      flashDropzone();
      await refresh().catch(() => {});
    } finally {
      state.localBusy = false;
      renderJob();
    }
  }

  async function uploadSingle(file) {
    const form = new FormData();
    form.append("file", file);
    return api("/source/upload", { method: "POST", form });
  }

  async function uploadBatch(files, name) {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    if (name) form.append("name", name);
    return api("/source/batch", { method: "POST", form });
  }

  async function runUpload(factory) {
    if (state.localBusy) return;
    state.localBusy = true;
    renderJob();
    try {
      state.source = await factory();
      await refresh();
    } catch (err) {
      showError(err.message);
      flashDropzone();
      await refresh().catch(() => {});
    } finally {
      state.localBusy = false;
      el.fileInput.value = "";
      el.dirInput.value = "";
      renderJob();
    }
  }

  async function acceptFiles(fileList, rootName) {
    const files = [...fileList];
    if (!files.length) return;

    // RAR в веб-части запрещён (ТЗ): просьба пересобрать архив в ZIP/7Z.
    if (files.some((f) => RAR_RE.test(f.name))) {
      showError("Файлы .rar не поддерживаются в веб-версии. Пересоберите архив в .zip или .7z.");
      flashDropzone();
      return;
    }

    const archives = files.filter((f) => ARCHIVE_RE.test(f.name));
    const images = files.filter((f) => IMAGE_RE.test(f.name));

    if (archives.length === 1 && images.length === 0) {
      await runUpload(() => uploadSingle(archives[0]));
      return;
    }
    if (archives.length > 0) {
      showError("Выберите один архив или папку с изображениями.");
      flashDropzone();
      return;
    }
    if (!images.length) {
      showError("Поддерживаются изображения PNG/JPG/BMP/WEBP/GIF/ICO/TIFF/AVIF и архивы ZIP/7Z.");
      flashDropzone();
      return;
    }
    await runUpload(() => uploadBatch(images, rootName || "Папка с изображениями"));
  }

  // Drag-and-Drop папки: File API не даёт путь, поэтому рекурсивно читаем
  // FileSystemEntry и отправляем найденные изображения в /api/source/batch.
  async function filesFromDrop(dataTransfer) {
    const entries = [];
    if (dataTransfer.items) {
      for (const item of dataTransfer.items) {
        const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
        if (entry) entries.push(entry);
      }
    }
    if (!entries.length) {
      return { files: [...dataTransfer.files], root: "" };
    }

    const files = [];
    let root = "";
    const walk = async (entry) => {
      if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        files.push(file);
        return;
      }
      if (entry.isDirectory) {
        if (!root) root = entry.name;
        const reader = entry.createReader();
        let batch;
        do {
          batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
          for (const child of batch) await walk(child);
        } while (batch.length);
      }
    };
    for (const entry of entries) await walk(entry);
    return { files, root };
  }

  // ------------------------------------------------------------------ запуск
  function setControlsBusy(busy) {
    const controls = [
      el.format,
      el.interp,
      el.quality,
      el.resizeEnabled,
      el.width,
      el.height,
      el.keepAspect,
      el.archiveBtn,
    ];
    for (const control of controls) control.disabled = busy;
    el.settingsPanel.classList.toggle("disabled", busy);
    if (!busy) updateDimsEnabled();
  }

  function renderJob() {
    const job = state.job || {};
    if (state.renderedStatus === "converting" && job.status === "done") beep();
    state.renderedStatus = job.status || null;

    const total = Number(job.total || 0);
    const done = Number(job.done || 0);
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const validating = state.localBusy || job.status === "validating";
    const converting = job.status === "converting";
    const busy = validating || converting;

    setControlsBusy(busy);
    el.progressTrack.classList.toggle("indeterminate", validating && !converting);
    if (!validating) el.progressFill.style.width = `${pct}%`;

    el.progressLabel.textContent =
      `Обработано: ${done} из ${total} файлов (${pct}% объектов)`;

    if (state.offline) {
      el.statusLabel.textContent = "Нет связи с API — запустите python server.py";
    } else if (state.localBusy) {
      el.statusLabel.textContent = "Загрузка и валидация источника…";
    } else if (job.status === "validating") {
      el.statusLabel.textContent = "Запуск сквозной валидации…";
    } else if (job.status === "ready") {
      el.statusLabel.textContent = "Валидация успешна. Нажмите «Старт».";
    } else if (job.status === "converting") {
      el.statusLabel.textContent = "Конвертация…";
    } else if (job.status === "done") {
      el.statusLabel.textContent = "Конвертация завершена.";
    } else if (job.status === "aborted") {
      el.statusLabel.textContent = "Прервано.";
    } else if (job.status === "error") {
      el.statusLabel.textContent = "Ошибка.";
    } else {
      el.statusLabel.textContent = "Ожидание источника…";
    }

    const ready = Boolean(state.source && state.source.ready);
    el.startBtn.disabled = !ready || busy || state.offline;
    el.abortBtn.disabled = !converting;
    // Скачать можно, только когда есть результаты конвертации.
    el.downloadBtn.disabled = !job.can_download || busy || state.offline;
    renderOut();
  }

  // ------------------------------------------------------------------ журнал
  function classify(line) {
    if (/ошибка|неподдерживаем/i.test(line)) return "error";
    if (/(^|\]) OK\b/.test(line)) return "ok";
    if (/прервано|abort/i.test(line)) return "warn";
    return "";
  }

  function showLogEmpty() {
    const node = document.createElement("div");
    node.className = "log-empty";
    node.textContent = "Журнал пуст. Выберите источник, чтобы начать.";
    el.log.replaceChildren(node);
  }

  function appendLog(message) {
    if (logFirstLoad) {
      logFirstLoad = false;
      el.log.replaceChildren();
    } else {
      const placeholder = el.log.querySelector(".log-empty");
      if (placeholder) placeholder.remove();
    }
    const node = document.createElement("div");
    const cls = classify(message);
    node.className = `log-line${cls ? ` ${cls}` : ""}`;
    node.textContent = message;
    el.log.append(node);
    while (el.log.childElementCount > 600) el.log.firstElementChild.remove();
    const nearBottom = el.log.scrollHeight - el.log.scrollTop - el.log.clientHeight < 80;
    if (nearBottom) el.log.scrollTop = el.log.scrollHeight;
  }

  function hydrateLog(lines) {
    el.log.replaceChildren();
    logFirstLoad = true;
    if (!Array.isArray(lines) || lines.length === 0) {
      showLogEmpty();
      return;
    }
    logFirstLoad = false;
    for (const line of lines) {
      const node = document.createElement("div");
      const cls = classify(line);
      node.className = `log-line${cls ? ` ${cls}` : ""}`;
      node.textContent = line;
      el.log.append(node);
    }
    el.log.scrollTop = el.log.scrollHeight;
  }

  // ------------------------------------------------------------------ события
  function handleEvent(event) {
    if (!event || typeof event !== "object") return;
    if (event.type === "log") {
      appendLog(event.message);
    } else if (event.type === "state") {
      state.job = event.job;
      renderJob();
    } else if (event.type === "source") {
      state.source = event.source;
      renderSource();
    }
  }

  function connectEvents() {
    const source = new EventSource(`${API_BASE}/api/events`);
    source.onopen = async () => {
      state.offline = false;
      // После реконнекта подтягиваем актуальный снимок, не трогая настройки.
      try {
        await syncData(false);
      } catch {
        /* сервер ещё поднимается */
      }
    };
    source.onerror = () => {
      state.offline = true;
      renderJob();
    };
    source.onmessage = (event) => {
      try {
        handleEvent(JSON.parse(event.data));
      } catch {
        /* пропускаем битый кадр */
      }
    };
  }

  // ------------------------------------------------------------------ действия
  async function browseFolder() {
    if (state.localBusy) return;
    const apiBridge = bridge();
    if (apiBridge && apiBridge.choose_folder) {
      try {
        const path = await apiBridge.choose_folder();
        if (path) await loadLocalPath(path);
      } catch (err) {
        showError(`Не удалось выбрать папку: ${err.message || err}`);
      }
      return;
    }
    // В браузере область импорта открывает обычный диалог выбора файла.
    // Для выбора папки остаётся отдельная поддержка Drag-and-Drop/dirInput.
    el.fileInput.click();
  }

  async function downloadResults() {
    if (el.downloadBtn.disabled) return;
    try {
      const res = await fetch(`${API_BASE}/api/download`);
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const data = await res.json();
          if (data && data.detail) detail = data.detail;
        } catch {
          /* не JSON — оставляем HTTP-статус */
        }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const dispo = res.headers.get("content-disposition") || "";
      const match = /filename="?([^";]+)"?/i.exec(dispo);
      const filename = match ? match[1] : "PixelForge-results.zip";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      appendLog(`Скачан архив результатов: ${filename}`);
    } catch (err) {
      showError(`Не удалось скачать результаты: ${err.message || err}`);
    }
  }

  async function chooseOutputDir() {
    if (state.localBusy) return;
    const apiBridge = bridge();
    if (!apiBridge || !apiBridge.choose_folder) {
      // Браузер не даёт выбирать пути — указываем путь вручную.
      // (Путь применяется на машине сервера.)
      const path = window.prompt(
        "Путь к папке вывода на машине сервера:",
        String(state.config.output_dir || "")
      );
      if (path === null) return;
      const trimmed = path.trim();
      state.config.output_dir = trimmed;
      renderOut();
      await persist().catch((err) => showError(err.message));
      return;
    }
    try {
      const path = await apiBridge.choose_folder();
      if (!path) return;
      state.config.output_dir = path;
      renderOut();
      await persist();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function startConversion() {
    if (state.localBusy) return;
    primeAudio();
    try {
      state.job = await api("/convert", { method: "POST", body: collectSettings() });
      renderJob();
    } catch (err) {
      showError(err.message);
    }
  }

  async function abortConversion() {
    try {
      state.job = await api("/abort", { method: "POST" });
      renderJob();
    } catch (err) {
      showError(err.message);
    }
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    el.format.addEventListener("change", schedulePersist);
    el.interp.addEventListener("change", schedulePersist);
    el.quality.addEventListener("input", () => {
      el.qualityValue.textContent = `${el.quality.value}%`;
      schedulePersist();
    });
    el.resizeEnabled.addEventListener("change", () => {
      updateDimsEnabled();
      schedulePersist();
    });
    el.width.addEventListener("input", schedulePersist);
    el.height.addEventListener("input", schedulePersist);
    el.keepAspect.addEventListener("change", schedulePersist);

    el.downloadBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      downloadResults();
    });
    el.archiveBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      el.fileInput.click();
    });
    el.dropzone.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      browseFolder();
    });
    el.dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        browseFolder();
      }
    });

    el.fileInput.addEventListener("change", () => {
      acceptFiles(el.fileInput.files, "");
    });
    el.dirInput.addEventListener("change", () => {
      const files = [...el.dirInput.files];
      const root = files[0]?.webkitRelativePath?.split("/")[0] || "Папка с изображениями";
      acceptFiles(files, root);
    });

    el.dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      el.dropzone.classList.add("drag-over");
    });
    el.dropzone.addEventListener("dragleave", () => {
      el.dropzone.classList.remove("drag-over");
    });
    el.dropzone.addEventListener("drop", async (event) => {
      event.preventDefault();
      el.dropzone.classList.remove("drag-over");
      try {
        const { files, root } = await filesFromDrop(event.dataTransfer);
        await acceptFiles(files, root);
      } catch (err) {
        showError(`Не удалось прочитать перетащенные файлы: ${err.message || err}`);
      }
    });
    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());

    el.startBtn.addEventListener("click", startConversion);
    el.abortBtn.addEventListener("click", abortConversion);

    el.modalOk.addEventListener("click", closeModal);
    el.modal.addEventListener("click", (event) => {
      if (event.target === el.modal) closeModal();
    });
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !el.modal.hidden) closeModal();
    });

    window.addEventListener("pywebviewready", () => {
      /* мост готов, кнопки уже работают через window.pywebview */
    });
  }

  async function boot() {
    wire();
    updateDimsEnabled();
    renderOut();
    try {
      await syncData(true);
    } catch (err) {
      state.offline = true;
      renderJob();
      appendLog(`API недоступен (${API_BASE || "same-origin"}): ${err.message}`);
      appendLog("Запустите сервер: python server.py");
    }
    connectEvents();
  }

  boot();
})();
