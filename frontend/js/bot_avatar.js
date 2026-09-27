/**
 * DataHunt AI Companion Bot Avatar  (v4.0 — PREMIUM NEXUS EDITION)
 *
 * Upgrades over v3.0:
 *  • UnrealBloomPass post-processing (ACES-filmic neon glow) with safe CDN fallback
 *  • MeshPhysicalMaterial armor with clearcoat for premium Pixar-toy sheen
 *  • Proper hexagonal chest badge (CylinderGeometry 6-sided)
 *  • Extended state machine: IDLE | LISTENING | SEARCHING | ANALYZING | RESPONDING | SUCCESS | ERROR
 *    (+ backward-compat aliases: STANDBY, PLANNING, COLLECTING, EXTRACTING, VERIFYING, COMPLETED, FAILED)
 *  • prefers-reduced-motion support (halts all animations gracefully)
 *  • dispose() method for memory hygiene
 *  • Helmet name-tag plate "DH-1" decal
 *  • Improved OLED visor: sharper eye glow, rounder pupils, cheek-blush shimmer
 *  • Dynamic rim light intensity per state (search = brighter cyan, error = red pulse)
 *  • Smoother blink (cubic ease instead of instant flip)
 *  • Instanced GPU nebula with 1400 pts and cyan/teal/indigo palette
 */

class AIBotAvatar {
  constructor(canvasId, options = {}) {
    this.canvas = typeof canvasId === "string" ? document.getElementById(canvasId) : canvasId;
    if (!this.canvas || typeof THREE === "undefined") {
      console.warn("[AIBotAvatar] Three.js or canvas element not available");
      return;
    }

    this.options = Object.assign({ enableControls: true, autoRotate: false }, options);
    this.clock   = new THREE.Clock();

    // Respect prefers-reduced-motion
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    this.reducedMotion = mq?.matches ?? false;
    mq?.addEventListener?.("change", e => { this.reducedMotion = e.matches; });

    // ── Scene ─────────────────────────────────────────────────────────
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x040810, 0.036);

    this.camera = new THREE.PerspectiveCamera(38, window.innerWidth / window.innerHeight, 0.1, 200);
    this.camera.position.set(0, 1.55, 5.2);

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance"
    });
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping        = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    if (this.options.enableControls && typeof THREE.OrbitControls !== "undefined") {
      this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping  = true;
      this.controls.dampingFactor  = 0.06;
      this.controls.maxPolarAngle  = Math.PI / 2 - 0.05;
      this.controls.minDistance    = 2.2;
      this.controls.maxDistance    = 9.0;
      this.controls.target.set(0, 1.0, 0);
    }

    // ── Canonical state machine ────────────────────────────────────────
    // Extended states + backward-compat aliases handled in setAgentState()
    this.agentState     = "STANDBY";
    this.currentEmotion = "happy";
    this.targetHead     = { x: 0, y: 0 };
    this.mouseNorm      = { x: 0, y: 0 };
    this.blinkTimer     = 0;
    this.blinkProgress  = 0;   // 0=open  1=closed  (smooth)
    this.isBlinking     = false;

    // ── Colour palette per state ─────────────────────────────────────
    this.STATE_COLORS = {
      // Canonical
      IDLE:       { hex: 0x00f0ff, css: "#00f0ff" },
      LISTENING:  { hex: 0x9d00ff, css: "#9d00ff" },
      SEARCHING:  { hex: 0x00cfff, css: "#00cfff" },
      ANALYZING:  { hex: 0x7000ff, css: "#7000ff" },
      RESPONDING: { hex: 0x00ffa3, css: "#00ffa3" },
      SUCCESS:    { hex: 0x00ffa3, css: "#00ffa3" },
      ERROR:      { hex: 0xff3366, css: "#ff3366" },
      // Backward-compat aliases
      STANDBY:    { hex: 0x00f0ff, css: "#00f0ff" },
      PLANNING:   { hex: 0x9d00ff, css: "#9d00ff" },
      COLLECTING: { hex: 0x00ffa3, css: "#00ffa3" },
      EXTRACTING: { hex: 0x7000ff, css: "#7000ff" },
      VERIFYING:  { hex: 0xffb800, css: "#ffb800" },
      COMPLETED:  { hex: 0x00ffa3, css: "#00ffa3" },
      FAILED:     { hex: 0xff3366, css: "#ff3366" },
    };

    // ── Groups ────────────────────────────────────────────────────────
    this.robotGroup    = new THREE.Group();
    this.robotGroup.scale.set(0.72, 0.72, 0.72);
    this.robotGroup.position.set(0, 0.2, 0);

    this.pedestalGroup = new THREE.Group();
    this.hologramGroup = new THREE.Group();
    this.vfxGroup      = new THREE.Group();
    this.shieldGroup   = new THREE.Group();

    // ── Lists ─────────────────────────────────────────────────────────
    this.orbitingNodes   = [];
    this.incomingPackets = [];
    this.radarRipples    = [];
    this.tendrils        = [];
    this.plasmaTrailPts  = [];
    this.matrixChars     = [];
    this._intervals      = [];   // for dispose() cleanup
    this._timeouts       = [];

    // ── Build ─────────────────────────────────────────────────────────
    this.initFaceCanvas();
    this.setupLighting();
    this.buildPedestal();
    this.buildRobot();
    this.buildHolographicConsole();
    this.buildHexShield();
    this.buildNebulaParticles();
    this.buildEnvironment();
    this.buildPlasmaTrail();

    this.scene.add(this.pedestalGroup);
    this.scene.add(this.robotGroup);
    this.scene.add(this.hologramGroup);
    this.scene.add(this.vfxGroup);
    this.scene.add(this.shieldGroup);

    this._setupPostprocessing();

    window.addEventListener("resize",    () => this.onWindowResize());
    window.addEventListener("mousemove", (e) => this.onMouseMove(e));

    this.animate();
  }

  // ════════════════════════════════════════════════════════════════════
  // 0. POST-PROCESSING (UnrealBloom — safe CDN fallback)
  // ════════════════════════════════════════════════════════════════════
  _setupPostprocessing() {
    this.composer = null;
    try {
      if (
        typeof THREE.EffectComposer !== "undefined" &&
        typeof THREE.RenderPass    !== "undefined" &&
        typeof THREE.UnrealBloomPass !== "undefined"
      ) {
        const renderPass = new THREE.RenderPass(this.scene, this.camera);
        const bloomPass  = new THREE.UnrealBloomPass(
          new THREE.Vector2(window.innerWidth, window.innerHeight),
          0.42,   // strength  — tasteful, not blinding
          0.28,   // radius
          0.82    // threshold — only bright neon emissives bloom
        );
        this.composer = new THREE.EffectComposer(this.renderer);
        this.composer.addPass(renderPass);
        this.composer.addPass(bloomPass);
        console.log("[AIBotAvatar] UnrealBloom post-processing active");
      }
    } catch (e) {
      console.warn("[AIBotAvatar] Bloom unavailable, using standard render:", e);
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // 1. PREMIUM OLED VISOR ENGINE
  // ════════════════════════════════════════════════════════════════════
  initFaceCanvas() {
    this.faceCanvas        = document.createElement("canvas");
    this.faceCanvas.width  = 512;
    this.faceCanvas.height = 256;
    this.faceCtx           = this.faceCanvas.getContext("2d");
    this.faceTexture       = new THREE.CanvasTexture(this.faceCanvas);
    this.faceTexture.minFilter = THREE.LinearMipmapLinearFilter;
    this.faceTexture.magFilter = THREE.LinearFilter;

    for (let i = 0; i < 24; i++) {
      this.matrixChars.push({
        x: Math.random() * 512,
        y: Math.random() * 256,
        speed: 40 + Math.random() * 80
      });
    }
    this.drawFace(0, 0);
  }

  drawFace(delta, time) {
    const ctx = this.faceCtx, w = 512, h = 256;
    const state = this.agentState;
    const col   = (this.STATE_COLORS[state] || this.STATE_COLORS.STANDBY).css;

    // Background — shifts per state
    ctx.clearRect(0, 0, w, h);
    const bg = ctx.createRadialGradient(w / 2, h / 2, 10, w / 2, h / 2, w * 0.55);
    if      (state === "VERIFYING"  || state === "ANALYZING")  { bg.addColorStop(0, "#1a1000"); bg.addColorStop(1, "#070400"); }
    else if (state === "COMPLETED"  || state === "SUCCESS")    { bg.addColorStop(0, "#001a0a"); bg.addColorStop(1, "#000f05"); }
    else if (state === "FAILED"     || state === "ERROR")      { bg.addColorStop(0, "#1a000a"); bg.addColorStop(1, "#0a0003"); }
    else if (state === "PLANNING"   || state === "LISTENING")  { bg.addColorStop(0, "#0d0029"); bg.addColorStop(1, "#040010"); }
    else                                                        { bg.addColorStop(0, "#091a3a"); bg.addColorStop(1, "#030a1e"); }
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    // Scanlines (subtle)
    for (let y = 0; y < h; y += 5) {
      ctx.fillStyle = "rgba(0,0,0,0.10)";
      ctx.fillRect(0, y, w, 2);
    }

    // Matrix rain (COLLECTING / EXTRACTING / ANALYZING)
    if (["COLLECTING","EXTRACTING","ANALYZING"].includes(state) || this.currentEmotion === "matrix") {
      const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789アイウエオカキクケコ";
      ctx.font = "14px 'Share Tech Mono',monospace";
      ctx.shadowBlur = 10;
      this.matrixChars.forEach(mc => {
        const alpha = 0.40 + 0.55 * Math.sin(time * 2 + mc.x);
        ctx.fillStyle = `rgba(0,255,163,${alpha.toFixed(2)})`;
        ctx.shadowColor = "#00ffa3";
        ctx.fillText(chars[Math.floor((time * mc.speed + mc.y) % chars.length)], mc.x, mc.y);
        mc.y += delta * mc.speed;
        if (mc.y > h) mc.y = 0;
      });
    }

    // Oscilloscope (SEARCHING)
    if (state === "SEARCHING" || this.currentEmotion === "scan") {
      ctx.strokeStyle = col;
      ctx.lineWidth   = 3;
      ctx.shadowColor = col;
      ctx.shadowBlur  = 16;
      ctx.beginPath();
      for (let x = 0; x < w; x++) {
        const y2 = h / 2 + Math.sin((x / w) * Math.PI * 8 + time * 8) * 28 +
                   Math.sin((x / w) * Math.PI * 3 + time * 3) * 12;
        x === 0 ? ctx.moveTo(x, y2) : ctx.lineTo(x, y2);
      }
      ctx.stroke();
      // Sweep bar
      const sweepX = (Math.sin(time * 2) * 0.5 + 0.5) * w;
      const sg = ctx.createLinearGradient(sweepX - 22, 0, sweepX + 22, 0);
      sg.addColorStop(0, "rgba(0,240,255,0)");
      sg.addColorStop(0.5, "rgba(0,240,255,0.88)");
      sg.addColorStop(1, "rgba(0,240,255,0)");
      ctx.fillStyle = sg;
      ctx.fillRect(sweepX - 22, 0, 44, h);
    }

    // ── Eye position (mouse tracking) ─────────────────────────────────
    const pupilX = this.mouseNorm.x * 18;
    const pupilY = -this.mouseNorm.y * 12;
    const lx = w / 2 - 96 + pupilX, rx = w / 2 + 96 + pupilX, ey = h / 2 + pupilY;

    ctx.strokeStyle = col;
    ctx.fillStyle   = col;
    ctx.shadowColor = col;
    ctx.shadowBlur  = 26;
    ctx.lineCap     = "round";

    // Smooth blink: scale eye Y by (1 - blinkProgress)
    const eyeScaleY = 1 - this.blinkProgress;

    if (eyeScaleY < 0.08) {
      // Fully closed — draw flat line blinks
      ctx.lineWidth = 10;
      ctx.shadowBlur = 8;
      [lx, rx].forEach(cx => {
        ctx.beginPath();
        ctx.moveTo(cx - 35, ey);
        ctx.lineTo(cx + 35, ey);
        ctx.stroke();
      });
      this.faceTexture.needsUpdate = true;
      return;
    }

    ctx.save();

    if (this.currentEmotion === "happy" || state === "STANDBY" || state === "IDLE") {
      ctx.lineWidth = 15;
      [lx, rx].forEach(cx => {
        ctx.save();
        ctx.translate(cx, ey + 10);
        ctx.scale(1, eyeScaleY);
        ctx.translate(-cx, -(ey + 10));
        ctx.beginPath();
        ctx.arc(cx, ey + 10, 42, Math.PI + 0.28, 2 * Math.PI - 0.28, false);
        ctx.stroke();
        ctx.restore();
      });
      // Blush cheeks
      ctx.shadowBlur = 6;
      ctx.fillStyle = "rgba(0,240,255,0.18)";
      ctx.beginPath(); ctx.ellipse(lx - 14, ey + 56, 20, 7, 0, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.ellipse(rx + 14, ey + 56, 20, 7, 0, 0, Math.PI * 2); ctx.fill();
    }
    else if (this.currentEmotion === "wink") {
      ctx.lineWidth = 14;
      ctx.save();
      ctx.translate(lx, ey + 10);
      ctx.scale(1, eyeScaleY);
      ctx.translate(-lx, -(ey + 10));
      ctx.beginPath();
      ctx.arc(lx, ey + 10, 40, Math.PI + 0.28, 2 * Math.PI - 0.28, false);
      ctx.stroke();
      ctx.restore();
      // Right eye — wink closed
      ctx.beginPath();
      ctx.moveTo(rx - 36, ey + 6);
      ctx.lineTo(rx + 26, ey + 6);
      ctx.lineTo(rx + 42, ey - 8);
      ctx.stroke();
    }
    else if (state === "PLANNING" || state === "LISTENING" || this.currentEmotion === "curious") {
      ctx.lineWidth = 12;
      [lx, rx].forEach(cx => {
        ctx.save();
        ctx.translate(cx, ey);
        ctx.scale(1, eyeScaleY);
        ctx.translate(-cx, -ey);
        ctx.beginPath();
        ctx.ellipse(cx, ey, 32, 44, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + pupilX * 0.4, ey + pupilY * 0.4, 17, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      });
      // Thinking dots
      const dotPhase = Math.floor(time * 3) % 4;
      ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 10;
      for (let i = 0; i < 3; i++) {
        if (i <= dotPhase) {
          ctx.beginPath();
          ctx.arc(w / 2 - 24 + i * 24, ey + 74, 5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
    else if (state === "VERIFYING" || state === "ANALYZING") {
      ctx.lineWidth = 11;
      ctx.strokeStyle = "#ffb800"; ctx.shadowColor = "#ffb800"; ctx.fillStyle = "#ffb800";
      [lx, rx].forEach(cx => {
        ctx.save();
        ctx.translate(cx, ey);
        ctx.scale(1, eyeScaleY);
        ctx.translate(-cx, -ey);
        ctx.beginPath();
        ctx.arc(cx, ey, 35, 0, Math.PI * 2);
        ctx.stroke();
        const a = time * 3;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * 40, ey + Math.sin(a) * 40);
        ctx.lineTo(cx + Math.cos(a + Math.PI) * 40, ey + Math.sin(a + Math.PI) * 40);
        ctx.stroke();
        ctx.restore();
      });
    }
    else if (state === "COMPLETED" || state === "SUCCESS" || this.currentEmotion === "love") {
      ctx.strokeStyle = "#00ffa3"; ctx.fillStyle = "#00ffa3";
      ctx.shadowColor = "#00ffa3"; ctx.lineWidth = 16;
      [lx, rx].forEach(cx => {
        ctx.save();
        ctx.translate(cx, ey + 14);
        ctx.scale(1, eyeScaleY);
        ctx.translate(-cx, -(ey + 14));
        ctx.beginPath();
        ctx.arc(cx, ey + 14, 44, Math.PI + 0.28, 2 * Math.PI - 0.28, false);
        ctx.stroke();
        ctx.restore();
      });
      // Star sparkles
      const pulse = 1 + Math.sin(time * 7) * 0.28;
      [[w / 2, ey - 52, 13], [lx - 54, ey - 28, 9], [rx + 54, ey - 28, 9]].forEach(([sx, sy, sz]) => {
        ctx.beginPath();
        ctx.moveTo(sx, sy - sz * pulse);
        ctx.quadraticCurveTo(sx, sy, sx + sz * pulse, sy);
        ctx.quadraticCurveTo(sx, sy, sx, sy + sz * pulse);
        ctx.quadraticCurveTo(sx, sy, sx - sz * pulse, sy);
        ctx.quadraticCurveTo(sx, sy, sx, sy - sz * pulse);
        ctx.fill();
      });
    }
    else if (state === "FAILED" || state === "ERROR" || this.currentEmotion === "alert") {
      ctx.strokeStyle = "#ff3366"; ctx.shadowColor = "#ff3366"; ctx.lineWidth = 14;
      [[lx, -1], [rx, 1]].forEach(([cx, s]) => {
        ctx.beginPath();
        ctx.moveTo(cx + s * 26, ey - 22);
        ctx.lineTo(cx - s * 14, ey);
        ctx.lineTo(cx + s * 26, ey + 22);
        ctx.stroke();
      });
    }
    else if (state === "RESPONDING") {
      // Mouth-shape wave bars
      ctx.lineWidth = 12;
      [lx, rx].forEach(cx => {
        ctx.save();
        ctx.translate(cx, ey);
        ctx.scale(1, eyeScaleY);
        ctx.translate(-cx, -ey);
        ctx.beginPath();
        ctx.arc(cx, ey, 34, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + pupilX * 0.5, ey + pupilY * 0.5, 15, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      });
      // Talking bars below eyes
      ctx.shadowBlur = 10;
      const bars = 5;
      for (let b = 0; b < bars; b++) {
        const bh = 6 + 14 * Math.abs(Math.sin(time * 8 + b * 1.3));
        ctx.fillStyle = col;
        ctx.fillRect(w / 2 - 36 + b * 18, h / 2 + 55 - bh / 2, 12, bh);
      }
    }
    else {
      // Fallback open circles
      ctx.lineWidth = 12;
      [lx, rx].forEach(cx => {
        ctx.save();
        ctx.translate(cx, ey);
        ctx.scale(1, eyeScaleY);
        ctx.translate(-cx, -ey);
        ctx.beginPath();
        ctx.arc(cx, ey, 34, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + pupilX * 0.5, ey + pupilY * 0.5, 14, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      });
    }

    ctx.restore();
    this.faceTexture.needsUpdate = true;
  }

  // ════════════════════════════════════════════════════════════════════
  // 2. STUDIO + DYNAMIC LIGHTING
  // ════════════════════════════════════════════════════════════════════
  setupLighting() {
    this.scene.add(new THREE.AmbientLight(0x10203a, 0.72));

    this.keyLight = new THREE.DirectionalLight(0xffffff, 1.1);
    this.keyLight.position.set(2.5, 4.5, 4.0);
    this.scene.add(this.keyLight);

    this.fillLight = new THREE.DirectionalLight(0x6080b0, 0.48);
    this.fillLight.position.set(-3.5, 2.5, 2.5);
    this.scene.add(this.fillLight);

    this.rimCyan = new THREE.DirectionalLight(0x00f0ff, 0.72);
    this.rimCyan.position.set(-2.5, 3.0, -3.5);
    this.scene.add(this.rimCyan);

    this.rimBlue = new THREE.DirectionalLight(0x3860ff, 0.52);
    this.rimBlue.position.set(2.5, 2.8, -3.5);
    this.scene.add(this.rimBlue);

    this.daisLight = new THREE.PointLight(0x00f0ff, 0.95, 3.5);
    this.daisLight.position.set(0, 0.4, 0);
    this.scene.add(this.daisLight);

    this.haloLight = new THREE.PointLight(0x00f0ff, 0.62, 2.0);
    this.haloLight.position.set(0, 3.2, 0.5);
    this.scene.add(this.haloLight);
  }

  // ════════════════════════════════════════════════════════════════════
  // 3. PEDESTAL
  // ════════════════════════════════════════════════════════════════════
  buildPedestal() {
    const baseGeo = new THREE.CylinderGeometry(1.6, 1.85, 0.16, 72);
    const baseMat = new THREE.MeshStandardMaterial({ color: 0x080d1c, roughness: 0.28, metalness: 0.9 });
    this.pedestalGroup.add(new THREE.Mesh(baseGeo, baseMat));

    const plateGeo = new THREE.CylinderGeometry(1.32, 1.32, 0.035, 56);
    const plateMat = new THREE.MeshPhysicalMaterial({
      color: 0x050b18, roughness: 0.12, metalness: 0.28,
      transmission: 0.65, transparent: true, opacity: 0.92
    });
    const plate = new THREE.Mesh(plateGeo, plateMat);
    plate.position.y = 0.09;
    this.pedestalGroup.add(plate);

    // Contact shadow
    const sc = document.createElement("canvas"); sc.width = sc.height = 128;
    const sctx = sc.getContext("2d");
    const sg = sctx.createRadialGradient(64, 64, 3, 64, 64, 60);
    sg.addColorStop(0, "rgba(0,0,0,0.9)"); sg.addColorStop(0.5, "rgba(0,0,0,0.45)"); sg.addColorStop(1, "rgba(0,0,0,0)");
    sctx.fillStyle = sg; sctx.fillRect(0, 0, 128, 128);
    const sm = new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sc), transparent: true, depthWrite: false });
    this.contactShadow = new THREE.Mesh(new THREE.PlaneGeometry(1.15, 1.15), sm);
    this.contactShadow.rotation.x = -Math.PI / 2;
    this.contactShadow.position.y = 0.098;
    this.pedestalGroup.add(this.contactShadow);

    // Outer neon ring
    this.outerRingMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide, transparent: true, opacity: 0.88 });
    this.outerPedestalRing = new THREE.Mesh(new THREE.RingGeometry(1.35, 1.45, 72), this.outerRingMat);
    this.outerPedestalRing.rotation.x = -Math.PI / 2; this.outerPedestalRing.position.y = 0.094;
    this.pedestalGroup.add(this.outerPedestalRing);

    // Inner hex ring
    this.innerHexMat = new THREE.MeshBasicMaterial({ color: 0x00d4de, side: THREE.DoubleSide, transparent: true, opacity: 0.68 });
    this.innerHexRing = new THREE.Mesh(new THREE.RingGeometry(0.82, 0.88, 6), this.innerHexMat);
    this.innerHexRing.rotation.x = -Math.PI / 2; this.innerHexRing.position.y = 0.096;
    this.pedestalGroup.add(this.innerHexRing);

    // Radar ripples (4 rings)
    for (let r = 0; r < 4; r++) {
      const rMat  = new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide, transparent: true, opacity: 0 });
      const rMesh = new THREE.Mesh(new THREE.RingGeometry(0.22, 0.28, 48), rMat);
      rMesh.rotation.x = -Math.PI / 2; rMesh.position.y = 0.099;
      this.pedestalGroup.add(rMesh);
      this.radarRipples.push({ mesh: rMesh, radius: 0.22 + r * 0.38, speed: 0.70, active: false });
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // 4. ROBOT BODY  (premium clearcoat PBR armor)
  // ════════════════════════════════════════════════════════════════════
  buildRobot() {
    // Premium satin pearl armor — clearcoat gives Pixar toy sheen
    this.armorMat = new THREE.MeshPhysicalMaterial({
      color: 0xecf2fa,
      roughness: 0.32,
      metalness: 0.04,
      clearcoat: 0.65,
      clearcoatRoughness: 0.18
    });

    this.tealMat = new THREE.MeshStandardMaterial({ color: 0x00c2cb, roughness: 0.22, metalness: 0.10 });
    this.darkMat = new THREE.MeshStandardMaterial({ color: 0x0b1c3a, roughness: 0.48, metalness: 0.42 });

    this.visorMat = new THREE.MeshPhysicalMaterial({
      color: 0xffffff, map: this.faceTexture,
      roughness: 0.06, metalness: 0.04,
      clearcoat: 1.0, clearcoatRoughness: 0.04,
      emissive: 0xffffff, emissiveMap: this.faceTexture, emissiveIntensity: 0.92
    });

    // ── HEAD ──────────────────────────────────────────────────────────
    this.head = new THREE.Group();
    this.head.position.set(0, 1.96, 0);

    const helmetGeo = new THREE.SphereGeometry(0.56, 48, 36);
    helmetGeo.scale(1.10, 1.02, 1.04);
    this.head.add(new THREE.Mesh(helmetGeo, this.armorMat));

    // Crown crest
    const crestGeo = new THREE.BoxGeometry(0.16, 0.09, 0.26);
    const crest = new THREE.Mesh(crestGeo, this.armorMat);
    crest.position.set(0, 0.56, -0.02);
    this.head.add(crest);

    // Name-tag plate "DH-1" on forehead
    const tagCanvas = document.createElement("canvas");
    tagCanvas.width = 128; tagCanvas.height = 32;
    const tagCtx = tagCanvas.getContext("2d");
    tagCtx.fillStyle = "#00f0ff"; tagCtx.font = "bold 18px 'Share Tech Mono',monospace";
    tagCtx.textAlign = "center"; tagCtx.fillText("DH-1", 64, 22);
    const tagTex = new THREE.CanvasTexture(tagCanvas);
    const tagMat = new THREE.MeshBasicMaterial({ map: tagTex, transparent: true, opacity: 0.88 });
    const tagMesh = new THREE.Mesh(new THREE.PlaneGeometry(0.18, 0.045), tagMat);
    tagMesh.position.set(0, 0.53, 0.40);
    this.head.add(tagMesh);

    // Visor
    const visorGeo = new THREE.SphereGeometry(0.48, 48, 32);
    visorGeo.scale(0.96, 0.78, 0.60);
    this.visorMesh = new THREE.Mesh(visorGeo, this.visorMat);
    this.visorMesh.position.set(0, 0.02, 0.22);
    this.head.add(this.visorMesh);

    // Laser scan cone
    const coneGeo = new THREE.ConeGeometry(0.56, 1.45, 36, 1, true);
    const coneMat = new THREE.MeshBasicMaterial({
      color: 0x00f0ff, transparent: true, opacity: 0.0,
      side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending
    });
    this.scannerBeam = new THREE.Mesh(coneGeo, coneMat);
    this.scannerBeam.position.set(0, -0.65, 0.75);
    this.scannerBeam.rotation.x = Math.PI / 3.2;
    this.head.add(this.scannerBeam);

    // Ear assemblies
    [-1, 1].forEach(s => {
      const eg = new THREE.Group(); eg.position.set(s * 0.57, 0.04, 0);
      const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.15, 0.13, 36), this.armorMat);
      cup.rotation.z = Math.PI / 2; eg.add(cup);
      const soc = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.14, 28), this.darkMat);
      soc.rotation.z = Math.PI / 2; eg.add(soc);
      const ringM = new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide });
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.035, 0.065, 28), ringM);
      ring.rotation.y = s * (Math.PI / 2); ring.position.x = s * 0.072; eg.add(ring);
      // Teal antenna fin
      const fg = new THREE.BoxGeometry(0.065, 0.26, 0.10);
      const fp = fg.attributes.position;
      for (let i = 0; i < fp.count; i++) {
        if (fp.getY(i) > 0.04) { fp.setX(i, fp.getX(i) * 0.40); fp.setZ(i, fp.getZ(i) * 0.45); }
      }
      fg.computeVertexNormals();
      const fin = new THREE.Mesh(fg, this.tealMat);
      fin.position.set(0, 0.15, -0.03); fin.rotation.z = s * -0.12; fin.rotation.x = -0.18;
      eg.add(fin);
      this.head.add(eg);
    });

    this.robotGroup.add(this.head);

    // ── TORSO ─────────────────────────────────────────────────────────
    const torso = new THREE.Group(); torso.position.set(0, 1.26, 0);

    const uGeo = new THREE.SphereGeometry(0.46, 36, 28); uGeo.scale(0.95, 1.02, 0.90);
    const upper = new THREE.Mesh(uGeo, this.armorMat); upper.position.y = 0.10; torso.add(upper);

    const lGeo = new THREE.SphereGeometry(0.42, 36, 28); lGeo.scale(0.86, 1.10, 0.82);
    const lower = new THREE.Mesh(lGeo, this.armorMat); lower.position.y = -0.22; torso.add(lower);

    const seam = new THREE.Mesh(new THREE.TorusGeometry(0.40, 0.012, 18, 56), this.darkMat);
    seam.rotation.x = Math.PI / 2; seam.position.y = -0.08; torso.add(seam);

    // ── PREMIUM HEXAGONAL CHEST BADGE ─────────────────────────────────
    // CylinderGeometry with 6 sides = perfect hexagon, no clipping needed
    const badgeHexGeo = new THREE.CylinderGeometry(0.115, 0.115, 0.028, 6);
    this.chestBadge = new THREE.Mesh(badgeHexGeo, new THREE.MeshStandardMaterial({
      color: 0x00c2cb,
      roughness: 0.18,
      metalness: 0.12,
      emissive: 0x00c2cb,
      emissiveIntensity: 0.5
    }));
    this.chestBadge.position.set(0, 0.06, 0.38);
    this.chestBadge.rotation.x = -Math.PI / 2 + 0.12;   // face front
    this.chestBadge.rotation.z = Math.PI / 6;            // flat-top hex orientation
    torso.add(this.chestBadge);

    // Inner glowing core of badge
    const badgeCoreMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff, blending: THREE.AdditiveBlending, transparent: true, opacity: 0.75 });
    const badgeCore = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.032, 6), badgeCoreMat);
    badgeCore.position.copy(this.chestBadge.position);
    badgeCore.rotation.copy(this.chestBadge.rotation);
    torso.add(badgeCore);

    // Thruster ring
    const thrM = new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide, transparent: true, opacity: 0.88 });
    this.thruster = new THREE.Mesh(new THREE.RingGeometry(0.10, 0.24, 36), thrM);
    this.thruster.rotation.x = Math.PI / 2; this.thruster.position.y = -0.58; torso.add(this.thruster);

    this.robotGroup.add(torso);

    // ── ARMS ──────────────────────────────────────────────────────────
    this.leftArm  = this._buildArm(-0.48, false);
    this.rightArm = this._buildArm( 0.48, true);
  }

  _buildArm(xOff, isRight) {
    const arm = new THREE.Group(); arm.position.set(xOff, 1.36, 0.02);
    const shoulder = new THREE.Mesh(new THREE.SphereGeometry(0.12, 24, 20), this.armorMat);
    arm.add(shoulder);
    const forearm = new THREE.Group();
    const ua = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.065, 0.32, 24), this.armorMat);
    ua.position.set(0, isRight ? 0.16 : -0.18, 0); forearm.add(ua);
    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.09, 24, 20), this.armorMat);
    hand.position.set(0, isRight ? 0.35 : -0.38, 0); forearm.add(hand);
    arm.add(forearm);
    if (isRight) {
      this.rightForearm = forearm;
      arm.rotation.z = -0.55; arm.rotation.x = -0.25;
    } else {
      this.leftForearm = forearm;
      arm.rotation.z = 0.22;
    }
    this.robotGroup.add(arm);
    return arm;
  }

  // ════════════════════════════════════════════════════════════════════
  // 5. HOLOGRAPHIC CONSOLE
  // ════════════════════════════════════════════════════════════════════
  buildHolographicConsole() {
    this.consoleGroup = new THREE.Group();
    this.consoleGroup.position.set(0, 0.95, 0.45);
    this.consoleGroup.rotation.x = -0.42;

    const kc = document.createElement("canvas"); kc.width = 256; kc.height = 128;
    const kctx = kc.getContext("2d");
    kctx.fillStyle = "rgba(0,240,255,0.06)"; kctx.fillRect(0, 0, 256, 128);
    kctx.strokeStyle = "rgba(0,240,255,0.55)"; kctx.lineWidth = 2; kctx.strokeRect(4, 4, 248, 120);
    kctx.fillStyle = "rgba(0,240,255,0.22)"; kctx.strokeStyle = "rgba(0,240,255,0.85)";
    for (let r = 0; r < 4; r++) for (let c = 0; c < 8; c++) {
      const kx = 14 + c * 29, ky = 14 + r * 26;
      kctx.fillRect(kx, ky, 26, 22); kctx.strokeRect(kx, ky, 26, 22);
    }
    const kbMat = new THREE.MeshBasicMaterial({
      map: new THREE.CanvasTexture(kc), transparent: true, opacity: 0.80,
      side: THREE.DoubleSide, blending: THREE.AdditiveBlending
    });
    this.keyboardMesh = new THREE.Mesh(new THREE.PlaneGeometry(0.92, 0.45, 12, 6), kbMat);
    this.consoleGroup.add(this.keyboardMesh);
    this.hologramGroup.add(this.consoleGroup);

    const screenMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff, wireframe: true, transparent: true, opacity: 0.30 });
    this.holoScreenLeft = new THREE.Mesh(new THREE.PlaneGeometry(1.0, 0.70, 8, 6), screenMat);
    this.holoScreenLeft.position.set(-1.45, 1.45, 0.25); this.holoScreenLeft.rotation.y = 0.50;
    this.hologramGroup.add(this.holoScreenLeft);

    this.holoScreenRight = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.65, 6, 5), screenMat.clone());
    this.holoScreenRight.position.set(1.40, 1.50, 0.20); this.holoScreenRight.rotation.y = -0.45;
    this.hologramGroup.add(this.holoScreenRight);

    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2, radius = 1.38;
      const hex = new THREE.Mesh(
        new THREE.RingGeometry(0.055, 0.08, 6),
        new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide, transparent: true, opacity: 0.65 })
      );
      hex.position.set(Math.cos(angle) * radius, 1.25 + Math.sin(i) * 0.15, Math.sin(angle) * radius);
      this.hologramGroup.add(hex);
      this.orbitingNodes.push({ mesh: hex, angle, speed: 0.38 + (i % 3) * 0.14, radius, baseY: 1.25 + (i % 2) * 0.15 });
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // 6. HEXAGONAL ENERGY SHIELD
  // ════════════════════════════════════════════════════════════════════
  buildHexShield() {
    this.shieldRings = [];
    const hexCounts = [6, 8, 10];
    const radii     = [0.85, 1.15, 1.48];
    const heights   = [1.1, 1.25, 1.40];

    hexCounts.forEach((count, ri) => {
      const ring = [];
      for (let i = 0; i < count; i++) {
        const angle = (i / count) * Math.PI * 2;
        const r = radii[ri];
        const geo = new THREE.RingGeometry(0.06, 0.10, 6);
        const mat = new THREE.MeshBasicMaterial({
          color: 0x00f0ff, side: THREE.DoubleSide,
          transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(Math.cos(angle) * r, heights[ri], Math.sin(angle) * r);
        mesh.lookAt(0, heights[ri], 0);
        this.shieldGroup.add(mesh);
        ring.push({ mesh, angle, radius: r, baseY: heights[ri], speed: (ri + 1) * 0.22 });
      }
      this.shieldRings.push(ring);
    });

    this.shieldLines = [];
    const oRing = this.shieldRings[2];
    for (let i = 0; i < oRing.length; i++) {
      const a = oRing[i].mesh.position;
      const b = oRing[(i + 1) % oRing.length].mesh.position;
      const geo = new THREE.BufferGeometry().setFromPoints([a.clone(), b.clone()]);
      const mat = new THREE.LineBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.0 });
      const line = new THREE.Line(geo, mat);
      this.shieldGroup.add(line);
      this.shieldLines.push(line);
    }

    this.shieldOpacity       = 0.0;
    this.shieldTargetOpacity = 0.0;
  }

  // ════════════════════════════════════════════════════════════════════
  // 7. NEBULA PARTICLES (1400 pts — cyan / teal / indigo)
  // ════════════════════════════════════════════════════════════════════
  buildNebulaParticles() {
    const COUNT = 1400;
    const pos   = new Float32Array(COUNT * 3);
    const col   = new Float32Array(COUNT * 3);

    for (let i = 0; i < COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi   = Math.acos(2 * Math.random() - 1);
      const r     = 3.5 + Math.random() * 6.0;
      pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.45;
      pos[i * 3 + 2] = r * Math.cos(phi);

      const t = Math.random();
      if (t < 0.45) { col[i*3]=0.0; col[i*3+1]=0.94; col[i*3+2]=1.0; }         // cyan
      else if (t < 0.72) { col[i*3]=0.0; col[i*3+1]=0.76; col[i*3+2]=0.55; }   // teal
      else if (t < 0.88) { col[i*3]=0.43; col[i*3+1]=0.0; col[i*3+2]=1.0; }    // indigo
      else { col[i*3]=0.8; col[i*3+1]=0.9; col[i*3+2]=1.0; }                   // white stars
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.020, vertexColors: true,
      transparent: true, opacity: 0.52,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    this.nebula = new THREE.Points(geo, mat);
    this.scene.add(this.nebula);
  }

  // ════════════════════════════════════════════════════════════════════
  // 8. PLASMA TRAIL
  // ════════════════════════════════════════════════════════════════════
  buildPlasmaTrail() {
    const TRAIL = 20;
    const pos   = new Float32Array(TRAIL * 3);
    const geo   = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const mat = new THREE.LineBasicMaterial({
      color: 0x00f0ff, transparent: true, opacity: 0.52, blending: THREE.AdditiveBlending
    });
    this.plasmaTrail = new THREE.Line(geo, mat);
    this.scene.add(this.plasmaTrail);
    this.trailPositions = [];
  }

  // ════════════════════════════════════════════════════════════════════
  // 9. ENVIRONMENT GRID + DUST
  // ════════════════════════════════════════════════════════════════════
  buildEnvironment() {
    const grid = new THREE.GridHelper(40, 80, 0x00f0ff, 0x081428);
    grid.position.y = -0.01;
    this.scene.add(grid);

    const N = 220, p = new Float32Array(N * 3);
    for (let i = 0; i < N * 3; i += 3) {
      p[i]     = (Math.random() - 0.5) * 12;
      p[i + 1] = Math.random() * 4.5;
      p[i + 2] = (Math.random() - 0.5) * 12;
    }
    const dGeo = new THREE.BufferGeometry(); dGeo.setAttribute("position", new THREE.BufferAttribute(p, 3));
    const dMat = new THREE.PointsMaterial({ size: 0.024, color: 0x00f0ff, transparent: true, opacity: 0.40, blending: THREE.AdditiveBlending });
    this.dust = new THREE.Points(dGeo, dMat);
    this.scene.add(this.dust);
  }

  // ════════════════════════════════════════════════════════════════════
  // 10. PUBLIC STATE API
  // ════════════════════════════════════════════════════════════════════
  setAgentState(state) {
    // Normalize aliases to canonical states for internal logic
    const aliases = {
      STANDBY:  "IDLE", PLANNING:  "LISTENING", COLLECTING: "SEARCHING",
      EXTRACTING: "ANALYZING", VERIFYING: "ANALYZING", COMPLETED: "SUCCESS",
      FAILED: "ERROR"
    };
    this.agentState = state || "IDLE";   // store raw for drawFace() state branch
    const col = (this.STATE_COLORS[state] || this.STATE_COLORS.IDLE).hex;

    this._applyColorScheme(col);

    const msgs = {
      IDLE:       "Ready for directives. Standing by… 👋",
      STANDBY:    "Ready for directives. Standing by… 👋",
      LISTENING:  "Formulating optimal search strategy… 🤔",
      PLANNING:   "Formulating optimal search strategy… 🤔",
      SEARCHING:  "Scanning career indices & web corpora… 🔍",
      COLLECTING: "Scanning career indices & web corpora… 🔍",
      ANALYZING:  "Synthesizing structured intelligence… 🧩",
      EXTRACTING: "Synthesizing structured intelligence… 🧩",
      VERIFYING:  "Running cryptographic provenance checks… 🛡️",
      RESPONDING: "Composing intelligence report… 📝",
      SUCCESS:    "Mission complete — all records verified! 🎉",
      COMPLETED:  "Mission complete — all records verified! 🎉",
      ERROR:      "Directive halted. Standing by for retry. ⚠️",
      FAILED:     "Directive halted. Standing by for retry. ⚠️",
    };
    this.speak(msgs[state] || "Processing…");

    const isActive = ["SEARCHING","COLLECTING","EXTRACTING","VERIFYING","ANALYZING"].includes(state);
    this.radarRipples.forEach(r => (r.active = isActive));
    this.shieldTargetOpacity = isActive ? 0.72 : 0.0;

    if (state === "SEARCHING" || state === "COLLECTING") {
      if (this.scannerBeam) this.scannerBeam.material.opacity = 0.30;
    } else if (this.scannerBeam) {
      this.scannerBeam.material.opacity = 0.0;
    }

    if (state === "COMPLETED" || state === "SUCCESS" || state === "FAILED" || state === "ERROR") {
      this._particleBurst(col);
    }

    // Dynamic rim light per state
    if (this.rimCyan) {
      if (state === "ERROR" || state === "FAILED") {
        this.rimCyan.color.setHex(0xff3366);
        this.rimCyan.intensity = 1.0;
      } else if (state === "SUCCESS" || state === "COMPLETED") {
        this.rimCyan.color.setHex(0x00ffa3);
        this.rimCyan.intensity = 1.1;
      } else {
        this.rimCyan.color.setHex(0x00f0ff);
        this.rimCyan.intensity = 0.72;
      }
    }
  }

  setEmotion(emotion) { this.currentEmotion = emotion || "happy"; }

  speak(text) {
    const el = document.getElementById("dialogue-speech-text");
    if (!el) return;
    el.textContent = "";
    clearInterval(this._typeInterval);
    let i = 0;
    this._typeInterval = setInterval(() => {
      el.textContent += text[i++];
      if (i >= text.length) clearInterval(this._typeInterval);
    }, 28);
  }

  // ════════════════════════════════════════════════════════════════════
  // 11. VFX EVENT HOOKS
  // ════════════════════════════════════════════════════════════════════
  onSearchResult(queryText) {
    this.radarRipples.forEach(r => (r.active = true));
    this.spawnIncomingDataPacket(0x00f0ff);
    if (queryText) {
      const t = queryText.length > 38 ? queryText.slice(0, 35) + "…" : queryText;
      this.speak(`Querying: "${t}" 🔍`);
    }
  }
  onPageFetched(url) {
    this.spawnIncomingDataPacket(0x00ffa3);
    try { this.speak(`Ingested: ${new URL(url).hostname} (200 OK) 📥`); }
    catch { this.speak("Ingested incoming document 📥"); }
  }
  onRecordExtracted(record) {
    this.spawnOrbitingExtractedNode(record);
    this._spawnTendril(0x00f0ff);
    const t = (record?.title || "Record").slice(0, 30);
    this.speak(`Extracted: ${t} 💎`);
  }
  onRecordVerified(record, isVerified) {
    const ok  = isVerified === true || isVerified === "VERIFIED" || isVerified === "PASS";
    const col = ok ? 0x00ffa3 : 0xffb800;
    this.spawnVerificationBeam(col);
    this._spawnTendril(col);
    this.speak(ok ? "Source cryptographically verified ✓" : "Verification pending ⚠️");
  }
  onRunCompleted() { this.setAgentState("SUCCESS"); }

  // ════════════════════════════════════════════════════════════════════
  // 12. VFX SPAWNERS
  // ════════════════════════════════════════════════════════════════════
  spawnOrbitingExtractedNode(record) {
    const mat  = new THREE.MeshStandardMaterial({ color: 0x00f0ff, roughness: 0.1, metalness: 0.8, emissive: 0x0044cc, emissiveIntensity: 0.9 });
    const mesh = new THREE.Mesh(new THREE.OctahedronGeometry(0.07, 0), mat);
    const angle = Math.random() * Math.PI * 2;
    const radius = 1.12 + Math.random() * 0.38;
    mesh.position.set(Math.cos(angle) * radius, 1.15 + Math.random() * 0.4, Math.sin(angle) * radius);
    this.vfxGroup.add(mesh);
    this.orbitingNodes.push({ mesh, angle, speed: 0.55 + Math.random() * 0.38, radius, baseY: 1.2 + (Math.random() - 0.5) * 0.3 });
    if (this.orbitingNodes.length > 18) {
      const old = this.orbitingNodes.shift();
      this.vfxGroup.remove(old.mesh);
      old.mesh.geometry.dispose(); old.mesh.material.dispose();
    }
  }

  spawnIncomingDataPacket(colorHex) {
    const mat    = new THREE.MeshBasicMaterial({ color: colorHex, wireframe: true });
    const packet = new THREE.Mesh(new THREE.BoxGeometry(0.065, 0.065, 0.065), mat);
    const angle  = Math.random() * Math.PI * 2;
    packet.position.set(Math.cos(angle) * 3.0, 1.3 + Math.random() * 0.6, Math.sin(angle) * 3.0);
    this.vfxGroup.add(packet);
    this.incomingPackets.push({ mesh: packet, progress: 0.0, startPos: packet.position.clone(), targetPos: new THREE.Vector3(0, 1.05, 0.3) });
  }

  spawnVerificationBeam(colorHex) {
    if (!this.orbitingNodes.length) return;
    const target = this.orbitingNodes[Math.floor(Math.random() * this.orbitingNodes.length)];
    const pts = [new THREE.Vector3(0, 1.55, 0.35), target.mesh.position.clone()];
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color: colorHex, transparent: true, opacity: 0.95 });
    const line = new THREE.Line(geo, mat);
    this.vfxGroup.add(line);
    const tid = setTimeout(() => { this.vfxGroup.remove(line); geo.dispose(); mat.dispose(); }, 480);
    this._timeouts.push(tid);
  }

  _spawnTendril(colorHex) {
    const pts = [];
    const startY = 1.0 + Math.random() * 0.8;
    const dir = (Math.random() - 0.5) * 2;
    for (let t = 0; t <= 12; t++) {
      const frac = t / 12;
      pts.push(new THREE.Vector3(
        dir * frac * (1.2 + Math.random() * 0.5),
        startY + frac * 0.6 + (Math.random() - 0.5) * 0.3,
        (Math.random() - 0.5) * 0.6
      ));
    }
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color: colorHex, transparent: true, opacity: 0.75, blending: THREE.AdditiveBlending });
    const line = new THREE.Line(geo, mat);
    this.vfxGroup.add(line);
    let life = 0;
    const iv = setInterval(() => {
      life += 0.06;
      mat.opacity = Math.max(0, 0.75 - life);
      if (life >= 0.75) { clearInterval(iv); this.vfxGroup.remove(line); geo.dispose(); mat.dispose(); }
    }, 30);
    this._intervals.push(iv);
  }

  _particleBurst(colorHex) {
    const N = 28;
    for (let i = 0; i < N; i++) {
      const geo  = new THREE.SphereGeometry(0.025, 4, 4);
      const mat  = new THREE.MeshBasicMaterial({ color: colorHex, blending: THREE.AdditiveBlending });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(0, 1.6, 0);
      this.vfxGroup.add(mesh);
      const vel = new THREE.Vector3((Math.random() - 0.5) * 3.5, Math.random() * 3.5, (Math.random() - 0.5) * 3.5);
      let life = 0;
      const iv = setInterval(() => {
        life += 0.04;
        mesh.position.addScaledVector(vel, 0.04);
        vel.y -= 0.05;
        mat.opacity = Math.max(0, 1 - life);
        if (life >= 1.0) { clearInterval(iv); this.vfxGroup.remove(mesh); geo.dispose(); mat.dispose(); }
      }, 16);
      this._intervals.push(iv);
    }
  }

  _applyColorScheme(hex) {
    if (this.outerRingMat) this.outerRingMat.color.setHex(hex);
    if (this.daisLight)    this.daisLight.color.setHex(hex);
    if (this.haloLight)    this.haloLight.color.setHex(hex);
    if (this.innerHexMat)  this.innerHexMat.color.setHex(hex);
    if (this.chestBadge)   { this.chestBadge.material.emissive.setHex(hex); this.chestBadge.material.color.setHex(hex); }
    this.shieldLines.forEach(l => l.material.color.setHex(hex));
    this.shieldRings.forEach(ring => ring.forEach(n => n.mesh.material.color.setHex(hex)));
  }

  // ════════════════════════════════════════════════════════════════════
  // 13. INPUT EVENTS
  // ════════════════════════════════════════════════════════════════════
  onMouseMove(e) {
    this.mouseNorm.x = (e.clientX / window.innerWidth) * 2 - 1;
    this.mouseNorm.y = -(e.clientY / window.innerHeight) * 2 + 1;
    this.targetHead.y = this.mouseNorm.x * 0.34;
    this.targetHead.x = -this.mouseNorm.y * 0.17;
  }
  onWindowResize() {
    this.camera.aspect = window.innerWidth / window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    if (this.composer) this.composer.setSize(window.innerWidth, window.innerHeight);
  }

  // ════════════════════════════════════════════════════════════════════
  // 14. DISPOSE (memory hygiene)
  // ════════════════════════════════════════════════════════════════════
  dispose() {
    this._intervals.forEach(iv => clearInterval(iv));
    this._timeouts.forEach(t => clearTimeout(t));
    clearInterval(this._typeInterval);
    window.removeEventListener("resize", this.onWindowResize);
    window.removeEventListener("mousemove", this.onMouseMove);
    this.renderer.dispose();
    if (this.composer) this.composer.dispose?.();
    this.faceTexture.dispose();
  }

  // ════════════════════════════════════════════════════════════════════
  // 15. MAIN ANIMATION LOOP
  // ════════════════════════════════════════════════════════════════════
  animate() {
    requestAnimationFrame(() => this.animate());
    if (document.hidden) return;

    const delta = this.clock.getDelta();
    const time  = this.clock.getElapsedTime();

    const isWorking = ["SEARCHING","COLLECTING","EXTRACTING","VERIFYING","ANALYZING"].includes(this.agentState);

    // ── Skip heavy animations if reduced motion ───────────────────────
    const motionScale = this.reducedMotion ? 0.15 : 1.0;

    // ── Hover bob ─────────────────────────────────────────────────────
    const bobFreq = isWorking ? 3.4 : 2.0;
    const hoverY  = motionScale * (0.055 * Math.sin(time * bobFreq) + 0.018 * Math.sin(time * 0.82));
    this.robotGroup.position.y = 0.20 + hoverY;

    if (this.contactShadow) {
      const s = 1.0 - (hoverY / 0.10) * 0.22;
      this.contactShadow.scale.set(s, s, 1);
    }

    // ── Plasma trail ──────────────────────────────────────────────────
    const botPos = new THREE.Vector3(0, this.robotGroup.position.y + 1.0, 0);
    this.trailPositions.unshift(botPos.clone());
    if (this.trailPositions.length > 20) this.trailPositions.length = 20;
    const trailAttr = this.plasmaTrail.geometry.attributes.position;
    for (let i = 0; i < 20; i++) {
      const p = this.trailPositions[i] || botPos;
      trailAttr.setXYZ(i, p.x, p.y - i * 0.014, p.z);
    }
    trailAttr.needsUpdate = true;
    this.plasmaTrail.material.opacity = isWorking ? 0.42 : 0.10;

    // ── Head motion ───────────────────────────────────────────────────
    if (this.head) {
      if (isWorking) {
        this.head.rotation.y += (Math.sin(time * 2.3) * 0.20 * motionScale - this.head.rotation.y) * 0.09;
        this.head.rotation.x += (-0.14 + Math.cos(time * 1.6) * 0.09 * motionScale - this.head.rotation.x) * 0.09;
      } else {
        this.head.rotation.y += (this.targetHead.y - this.head.rotation.y) * 0.09;
        this.head.rotation.x += (this.targetHead.x - this.head.rotation.x) * 0.09;
      }
    }

    // ── Smooth blink ─────────────────────────────────────────────────
    this.blinkTimer += delta;
    if (this.blinkTimer > 4.5 && !this.isBlinking) {
      this.isBlinking = true;
    }
    if (this.isBlinking) {
      // Cubic ease: close then open (each half ~90ms)
      const blinkDur = 0.18;
      const halfBlinkTimer = this.blinkTimer - 4.5;
      if (halfBlinkTimer < blinkDur / 2) {
        this.blinkProgress = Math.pow(halfBlinkTimer / (blinkDur / 2), 2);  // ease in (close)
      } else if (halfBlinkTimer < blinkDur) {
        this.blinkProgress = Math.pow(1 - (halfBlinkTimer - blinkDur / 2) / (blinkDur / 2), 2);  // ease out (open)
      } else {
        this.blinkProgress = 0;
        this.isBlinking    = false;
        this.blinkTimer    = 0;
      }
    } else {
      this.blinkProgress = 0;
    }
    this.drawFace(delta, time);

    // ── Arm gestures (8 states) ───────────────────────────────────────
    if (this.leftArm && this.rightArm && this.rightForearm) {
      const mS = motionScale;
      if (isWorking) {
        const tL = Math.sin(time * 14.0) * mS, tR = Math.cos(time * 14.0) * mS;
        this.leftArm.rotation.x  = -0.74 + tL * 0.13;
        this.leftArm.rotation.z  =  0.32;
        this.leftArm.rotation.y  =  0.14;
        this.rightArm.rotation.x = -0.74 + tR * 0.13;
        this.rightArm.rotation.z = -0.32;
        this.rightArm.rotation.y = -0.14;
        this.rightForearm.rotation.z = tR * 0.14;
        this.rightForearm.rotation.x = 0;
        if (this.keyboardMesh) this.keyboardMesh.material.opacity = 0.72 + Math.sin(time * 8) * 0.20 * mS;
      } else if (this.agentState === "COMPLETED" || this.agentState === "SUCCESS") {
        const wave = Math.sin(time * 7.5) * 0.30 * mS;
        this.rightArm.rotation.z += (-0.70 + wave - this.rightArm.rotation.z) * 0.14;
        this.rightArm.rotation.x += (-0.28 - this.rightArm.rotation.x) * 0.10;
        this.leftArm.rotation.z   = 0.70 - wave;
        this.leftArm.rotation.x   = -0.28;
        this.rightForearm.rotation.z = Math.sin(time * 9) * 0.35 * mS;
      } else if (this.agentState === "FAILED" || this.agentState === "ERROR") {
        this.leftArm.rotation.x  = 0.35;
        this.leftArm.rotation.z  = 0.18;
        this.rightArm.rotation.x = 0.35;
        this.rightArm.rotation.z = -0.18;
        this.rightForearm.rotation.z = 0;
      } else if (this.agentState === "PLANNING" || this.agentState === "LISTENING") {
        this.rightArm.rotation.x += (-0.55 - this.rightArm.rotation.x) * 0.08;
        this.rightArm.rotation.z += (-0.20 - this.rightArm.rotation.z) * 0.08;
        this.rightForearm.rotation.z = Math.sin(time * 1.5) * 0.10 * mS;
        this.leftArm.rotation.x = 0.05;
        this.leftArm.rotation.z = 0.22;
      } else if (this.agentState === "RESPONDING") {
        // Point outward
        this.rightArm.rotation.x += (-0.35 - this.rightArm.rotation.x) * 0.08;
        this.rightArm.rotation.z += (-0.40 - this.rightArm.rotation.z) * 0.08;
        this.rightForearm.rotation.z = Math.sin(time * 2.5) * 0.15 * mS;
        this.leftArm.rotation.x = 0.05;
        this.leftArm.rotation.z = 0.22;
      } else {
        // STANDBY / IDLE — classic right hand wave
        const wZ = -0.55 + Math.sin(time * 5.2) * 0.22 * mS;
        const wX = -0.30 + Math.cos(time * 2.6) * 0.09 * mS;
        this.rightArm.rotation.z += (wZ - this.rightArm.rotation.z) * 0.10;
        this.rightArm.rotation.x += (wX - this.rightArm.rotation.x) * 0.10;
        this.rightForearm.rotation.z = Math.sin(time * 6.2) * 0.22 * mS;
        this.leftArm.rotation.x = Math.sin(time * 1.9) * 0.06 * mS;
        this.leftArm.rotation.z = 0.22;
        this.leftArm.rotation.y = 0;
      }
    }

    // ── Chest badge pulse ─────────────────────────────────────────────
    if (this.chestBadge) {
      const pulse = 0.45 + 0.30 * Math.abs(Math.sin(time * 2.5));
      this.chestBadge.material.emissiveIntensity = pulse;
    }

    // ── Visor scan beam ───────────────────────────────────────────────
    if (this.scannerBeam && this.scannerBeam.material.opacity > 0.01) {
      this.scannerBeam.rotation.z = Math.sin(time * 5.2) * 0.16 * motionScale;
    }

    // ── Pedestal rings ────────────────────────────────────────────────
    if (this.outerPedestalRing) this.outerPedestalRing.rotation.z += 0.28 * delta * motionScale;
    if (this.innerHexRing)      this.innerHexRing.rotation.z      -= 0.40 * delta * motionScale;

    // ── Radar ripples ─────────────────────────────────────────────────
    this.radarRipples.forEach(r => {
      if (r.active) {
        r.radius += r.speed * delta;
        r.mesh.scale.set(r.radius, r.radius, 1);
        r.mesh.material.opacity = Math.max(0, 0.85 - (r.radius / 1.6) * 0.85);
        if (r.radius > 1.6) {
          r.radius = 0.22;
          const stillActive = ["SEARCHING","COLLECTING","EXTRACTING","VERIFYING","ANALYZING"].includes(this.agentState);
          if (!stillActive) { r.active = false; r.mesh.material.opacity = 0; }
        }
      }
    });

    // ── Holo screens float ────────────────────────────────────────────
    if (this.holoScreenLeft)  this.holoScreenLeft.position.y  = 1.45 + Math.sin(time * 2.0) * 0.044 * motionScale;
    if (this.holoScreenRight) this.holoScreenRight.position.y = 1.50 + Math.cos(time * 1.8) * 0.044 * motionScale;

    // ── Orbiting nodes ────────────────────────────────────────────────
    this.orbitingNodes.forEach(n => {
      n.angle += n.speed * delta * motionScale;
      n.mesh.position.x = Math.cos(n.angle) * n.radius;
      n.mesh.position.z = Math.sin(n.angle) * n.radius;
      n.mesh.position.y = n.baseY + Math.sin(time * 2.6 + n.angle) * 0.065 * motionScale;
      n.mesh.rotation.y += delta * 1.6 * motionScale;
    });

    // ── Incoming data packets ─────────────────────────────────────────
    for (let i = this.incomingPackets.length - 1; i >= 0; i--) {
      const p = this.incomingPackets[i];
      p.progress += delta * 1.9;
      p.mesh.position.lerpVectors(p.startPos, p.targetPos, p.progress);
      p.mesh.rotation.x += delta * 7; p.mesh.rotation.y += delta * 7;
      if (p.progress >= 1.0) {
        this.vfxGroup.remove(p.mesh); p.mesh.geometry.dispose(); p.mesh.material.dispose();
        this.incomingPackets.splice(i, 1);
      }
    }

    // ── Hex shield animation ──────────────────────────────────────────
    this.shieldOpacity += (this.shieldTargetOpacity - this.shieldOpacity) * 0.06;
    this.shieldRings.forEach((ring, ri) => {
      ring.forEach((n, idx) => {
        n.angle += n.speed * delta * motionScale * (ri % 2 === 0 ? 1 : -1);
        n.mesh.position.x = Math.cos(n.angle) * n.radius;
        n.mesh.position.z = Math.sin(n.angle) * n.radius;
        n.mesh.position.y = n.baseY + Math.sin(time * 1.5 + idx) * 0.05 * motionScale;
        const pulse = this.shieldOpacity * (0.65 + 0.35 * Math.sin(time * 4 + ri));
        n.mesh.material.opacity = pulse;
        n.mesh.lookAt(0, n.baseY, 0);
      });
    });
    this.shieldLines.forEach(l => l.material.opacity = this.shieldOpacity * 0.45);

    // ── Nebula drift ──────────────────────────────────────────────────
    if (this.nebula) {
      this.nebula.rotation.y += 0.004 * delta * motionScale;
      this.nebula.rotation.x  = Math.sin(time * 0.05) * 0.06;
    }
    if (this.dust) this.dust.rotation.y += 0.012 * delta * motionScale;

    if (this.controls) this.controls.update();

    // ── Render (bloom or standard) ─────────────────────────────────────
    if (this.composer) {
      this.composer.render();
    } else {
      this.renderer.render(this.scene, this.camera);
    }
  }
}

window.AIBotAvatar = AIBotAvatar;
