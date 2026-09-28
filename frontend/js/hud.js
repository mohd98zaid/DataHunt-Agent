// HUD Controller: Audio/Telemetry Waveform, Confidence Radial Meter, Log Stream

class HUDController {
  constructor() {
    this.waveformCanvas = document.getElementById("waveform-canvas");
    this.ctx = this.waveformCanvas ? this.waveformCanvas.getContext("2d") : null;
    this.radialProgress = document.getElementById("radial-progress");
    this.radialValueText = document.getElementById("radial-value-text");
    this.terminalLog = document.getElementById("terminal-logs");

    this.waveformBars = 32;
    this.waveData = new Array(this.waveformBars).fill(10);
    this.waveActivity = 0.3; // multiplier

    this.initWaveform();
  }

  initWaveform() {
    if (!this.waveformCanvas) return;
    this.resizeWaveform();
    window.addEventListener("resize", () => this.resizeWaveform());
    this.animateWaveform();
  }

  resizeWaveform() {
    if (!this.waveformCanvas) return;
    this.waveformCanvas.width  = this.waveformCanvas.clientWidth  * window.devicePixelRatio;
    this.waveformCanvas.height = this.waveformCanvas.clientHeight * window.devicePixelRatio;
    // Pre-compute shared gradient so we never allocate inside the hot loop
    this._cachedGrad = null;  // force regeneration on next paint
  }

  setWaveformActivity(level) {
    this.waveActivity = Math.max(0.2, Math.min(level, 1.5));
  }


  _getGradient(y, barH) {
    // Reuse a single gradient object (re-created only when canvas dimensions change)
    if (!this._cachedGrad ||
        this._gradY !== y || this._gradBarH !== barH) {
      const h = this.waveformCanvas.height;
      const midY = (h - barH) / 2;
      this._cachedGrad = this.ctx.createLinearGradient(0, midY, 0, midY + barH);
      this._cachedGrad.addColorStop(0, "rgba(0, 240, 255, 0.9)");
      this._cachedGrad.addColorStop(1, "rgba(0, 114, 255, 0.4)");
      this._gradY    = y;
      this._gradBarH = barH;
    }
    return this._cachedGrad;
  }

  animateWaveform() {
    requestAnimationFrame(() => this.animateWaveform());
    // Skip rendering when tab is hidden — saves 60 canvas redraws/sec in background
    if (document.hidden) return;
    if (!this.ctx || !this.waveformCanvas) return;

    const width  = this.waveformCanvas.width;
    const height = this.waveformCanvas.height;
    this.ctx.clearRect(0, 0, width, height);

    const barWidth = width / this.waveformBars;
    const now      = Date.now();

    for (let i = 0; i < this.waveformBars; i++) {
      const targetHeight = (Math.sin(now * 0.005 + i * 0.3) * 0.5 + 0.5) *
                           (height * 0.7) * this.waveActivity + (Math.random() * 8);
      this.waveData[i] += (targetHeight - this.waveData[i]) * 0.2;

      const barH = Math.max(4, this.waveData[i]);
      const x    = i * barWidth;
      const y    = (height - barH) / 2;

      // Use pre-computed gradient — no per-bar allocation
      this.ctx.fillStyle = this._getGradient(y, barH);
      this.ctx.fillRect(x + 2, y, barWidth - 4, barH);
    }
  }

  updateConfidence(percent) {
    if (!this.radialProgress || !this.radialValueText) return;
    const clamped = Math.max(0, Math.min(100, Math.round(percent)));
    this.radialValueText.textContent = `${clamped}%`;

    // 220 is stroke-dasharray
    const offset = 220 - (220 * clamped) / 100;
    this.radialProgress.style.strokeDashoffset = offset;

    if (clamped >= 75) {
      this.radialProgress.style.stroke = "var(--neon-green)";
    } else if (clamped >= 40) {
      this.radialProgress.style.stroke = "var(--neon-cyan)";
    } else {
      this.radialProgress.style.stroke = "var(--neon-amber)";
    }
  }

  appendLog(type, message, durationMs = null) {
    if (!this.terminalLog) return;

    const now = new Date();
    const timeStr = `[${now.toTimeString().split(" ")[0]}]`;

    const entry = document.createElement("div");
    entry.className = "log-entry";

    const durText = durationMs !== null ? `${durationMs}ms` : "";

    const timeSpan = document.createElement("span");
    timeSpan.className = "log-time";
    timeSpan.textContent = timeStr;

    const typeSpan = document.createElement("span");
    typeSpan.className = "log-type";
    typeSpan.textContent = type;

    const msgSpan = document.createElement("span");
    msgSpan.className = "log-msg";
    msgSpan.textContent = message;

    entry.appendChild(timeSpan);
    entry.appendChild(document.createTextNode(" "));
    entry.appendChild(typeSpan);
    entry.appendChild(document.createTextNode(" "));
    entry.appendChild(msgSpan);

    if (durText) {
      const durSpan = document.createElement("span");
      durSpan.className = "log-dur";
      durSpan.textContent = durText;
      entry.appendChild(document.createTextNode(" "));
      entry.appendChild(durSpan);
    }

    this.terminalLog.appendChild(entry);
    this.terminalLog.scrollTop = this.terminalLog.scrollHeight;
  }

  setPhaseState(phase) {
    const phases = ["PLANNING", "SEARCHING", "COLLECTING", "EXTRACTING", "VERIFYING", "DEDUPLICATING", "EXPORTING"];
    const targetIdx = phases.indexOf(phase);

    phases.forEach((p, idx) => {
      const el = document.getElementById(`phase-node-${p}`);
      if (!el) return;
      const badge = el.querySelector(".phase-badge");

      if (idx < targetIdx) {
        el.className = "phase-node completed";
        if (badge) badge.textContent = "DONE";
      } else if (idx === targetIdx) {
        el.className = "phase-node active";
        if (badge) badge.textContent = "ACTIVE";
      } else {
        el.className = "phase-node";
        if (badge) badge.textContent = "IDLE";
      }
    });
  }
}

window.HUDController = HUDController;
