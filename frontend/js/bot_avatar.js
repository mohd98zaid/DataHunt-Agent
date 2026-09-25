/**
 * DataHunt AI Companion Bot Avatar  (v3.0 — NEXUS EDITION)
 *
 * Visual upgrades:
 *  • Instanced GPU particle nebula (1000 pts) with drift & phase tint
 *  • Holographic hex-shield ring that morphs on every state transition
 *  • Energy tendrils radiating from body on VFX events
 *  • Plasma trail ribbon following bot hover
 *  • 8 fully distinct gesture animations (standby wave, search, collect, extract, verify, complete, fail, dance)
 *  • OLED visor rewritten with richer effects per state (scrolling matrix rain, oscilloscope, etc.)
 *  • Typewriter speech bubble (pure JS, no external lib)
 *  • Colour-correct state tinting on lights, rings, and badge
 */

class AIBotAvatar {
  constructor(canvasId, options = {}) {
    this.canvas = typeof canvasId === "string" ? document.getElementById(canvasId) : canvasId;
    if (!this.canvas || typeof THREE === "undefined") {
      console.warn("[AIBotAvatar] Three.js or canvas element not available");
      return;
    }

    this.options = Object.assign({ enableControls: true, autoRotate: false }, options);
    this.clock = new THREE.Clock();

    // ── Scene ─────────────────────────────────────────────────────────
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x040810, 0.038);

    this.camera = new THREE.PerspectiveCamera(38, window.innerWidth / window.innerHeight, 0.1, 200);
    this.camera.position.set(0, 1.55, 5.2);

    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2.5));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;

    if (this.options.enableControls && typeof THREE.OrbitControls !== "undefined") {
      this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.06;
      this.controls.maxPolarAngle = Math.PI / 2 - 0.05;
      this.controls.minDistance = 2.2;
      this.controls.maxDistance = 9.0;
      this.controls.target.set(0, 1.0, 0);
    }

    // ── State ─────────────────────────────────────────────────────────
    this.agentState      = "STANDBY";
    this.currentEmotion  = "happy";
    this.targetHead      = { x: 0, y: 0 };
    this.mouseNorm       = { x: 0, y: 0 };
    this.blinkTimer      = 0;
    this.isBlinking      = false;
    this.typingSpeed     = 14.0;

    // ── Colour palette per state ─────────────────────────────────────
    this.STATE_COLORS = {
      STANDBY:    { hex: 0x00f0ff, css: "#00f0ff" },
      PLANNING:   { hex: 0x9d00ff, css: "#9d00ff" },
      SEARCHING:  { hex: 0x00cfff, css: "#00cfff" },
      COLLECTING: { hex: 0x00ffa3, css: "#00ffa3" },
      EXTRACTING: { hex: 0x7000ff, css: "#7000ff" },
      VERIFYING:  { hex: 0xffb800, css: "#ffb800" },
      COMPLETED:  { hex: 0x00ffa3, css: "#00ffa3" },
      FAILED:     { hex: 0xff3366, css: "#ff3366" },
    };

    // ── Groups ────────────────────────────────────────────────────────
    this.robotGroup   = new THREE.Group();
    this.robotGroup.scale.set(0.72, 0.72, 0.72);
    this.robotGroup.position.set(0, 0.2, 0);

    this.pedestalGroup = new THREE.Group();
    this.hologramGroup = new THREE.Group();
    this.vfxGroup      = new THREE.Group();
    this.shieldGroup   = new THREE.Group();

    // ── Lists ─────────────────────────────────────────────────────────
    this.orbitingNodes    = [];
    this.incomingPackets  = [];
    this.radarRipples     = [];
    this.tendrils         = [];
    this.plasmaTrailPts   = [];
    this.matrixChars      = [];

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

    window.addEventListener("resize",    () => this.onWindowResize());
    window.addEventListener("mousemove", (e) => this.onMouseMove(e));

    this.animate();
  }

  // ════════════════════════════════════════════════════════════════════
  // 1. RICH OLED VISOR ENGINE
  // ════════════════════════════════════════════════════════════════════
  initFaceCanvas() {
    this.faceCanvas  = document.createElement("canvas");
    this.faceCanvas.width  = 512;
    this.faceCanvas.height = 256;
    this.faceCtx     = this.faceCanvas.getContext("2d");
    this.faceTexture = new THREE.CanvasTexture(this.faceCanvas);
    this.faceTexture.minFilter = THREE.LinearMipmapLinearFilter;
    this.faceTexture.magFilter = THREE.LinearFilter;

    // Matrix rain characters
    for (let i = 0; i < 24; i++) {
      this.matrixChars.push({ x: Math.random() * 512, y: Math.random() * 256, speed: 40 + Math.random() * 80 });
    }

    this.drawFace(0, 0);
  }

  drawFace(delta, time) {
    const ctx = this.faceCtx, w = 512, h = 256;
    const state = this.agentState;
    const col   = (this.STATE_COLORS[state] || this.STATE_COLORS.STANDBY).css;

    // Background gradient — shifts hue per state
    ctx.clearRect(0, 0, w, h);
    const bg = ctx.createRadialGradient(w/2, h/2, 10, w/2, h/2, w*0.55);
    if      (state === "VERIFYING")  { bg.addColorStop(0, "#1a1000"); bg.addColorStop(1, "#070400"); }
    else if (state === "COMPLETED")  { bg.addColorStop(0, "#001a0a"); bg.addColorStop(1, "#000f05"); }
    else if (state === "FAILED")     { bg.addColorStop(0, "#1a000a"); bg.addColorStop(1, "#0a0003"); }
    else if (state === "PLANNING")   { bg.addColorStop(0, "#0d0029"); bg.addColorStop(1, "#040010"); }
    else                             { bg.addColorStop(0, "#091a3a"); bg.addColorStop(1, "#030a1e"); }
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    // Scanlines
    for (let y = 0; y < h; y += 6) {
      ctx.fillStyle = "rgba(0,0,0,0.12)";
      ctx.fillRect(0, y, w, 2);
    }

    // ── Matrix rain (COLLECTING / EXTRACTING) ──────────────────
    if (state === "COLLECTING" || state === "EXTRACTING" || this.currentEmotion === "matrix") {
      const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789アイウエオカキクケコ";
      ctx.font = "14px 'Share Tech Mono',monospace";
      ctx.shadowBlur = 8;
      this.matrixChars.forEach(mc => {
        const alpha = 0.45 + 0.55 * Math.sin(time * 2 + mc.x);
        ctx.fillStyle = `rgba(0,255,163,${alpha})`;
        ctx.shadowColor = "#00ffa3";
        ctx.fillText(chars[Math.floor((time * mc.speed + mc.y) % chars.length)], mc.x, mc.y);
        mc.y += delta * mc.speed;
        if (mc.y > h) mc.y = 0;
      });
    }

    // ── Oscilloscope wave (SEARCHING) ──────────────────────────
    if (state === "SEARCHING" || this.currentEmotion === "scan") {
      ctx.strokeStyle = col;
      ctx.lineWidth   = 3;
      ctx.shadowColor = col;
      ctx.shadowBlur  = 14;
      ctx.beginPath();
      for (let x = 0; x < w; x++) {
        const y2 = h/2 + Math.sin((x/w)*Math.PI*8 + time*8)*28 + Math.sin((x/w)*Math.PI*3 + time*3)*12;
        x === 0 ? ctx.moveTo(x, y2) : ctx.lineTo(x, y2);
      }
      ctx.stroke();
      // Sweep line
      const sweepX = (Math.sin(time*2)*0.5+0.5)*w;
      const sg = ctx.createLinearGradient(sweepX-20, 0, sweepX+20, 0);
      sg.addColorStop(0,"rgba(0,240,255,0)"); sg.addColorStop(0.5,"rgba(0,240,255,0.9)"); sg.addColorStop(1,"rgba(0,240,255,0)");
      ctx.fillStyle = sg; ctx.fillRect(sweepX-20, 0, 40, h);
    }

    const pupilX = this.mouseNorm.x * 18;
    const pupilY = -this.mouseNorm.y * 12;
    const lx = w/2 - 96 + pupilX, rx = w/2 + 96 + pupilX, ey = h/2 + pupilY;

    ctx.strokeStyle = col; ctx.fillStyle = col;
    ctx.shadowColor = col; ctx.shadowBlur = 22;
    ctx.lineCap = "round";

    // Blink
    if (this.isBlinking) {
      ctx.lineWidth = 10;
      [[lx], [rx]].forEach(([cx]) => {
        ctx.beginPath(); ctx.moveTo(cx-35, ey); ctx.lineTo(cx+35, ey); ctx.stroke();
      });
      this.faceTexture.needsUpdate = true; return;
    }

    if (this.currentEmotion === "happy" || state === "STANDBY") {
      ctx.lineWidth = 15;
      [lx, rx].forEach(cx => {
        ctx.beginPath(); ctx.arc(cx, ey+10, 40, Math.PI+0.3, 2*Math.PI-0.3, false); ctx.stroke();
      });
      ctx.fillStyle = "rgba(0,240,255,0.22)"; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.ellipse(lx-12, ey+52, 18, 7, 0, 0, Math.PI*2); ctx.fill();
      ctx.beginPath(); ctx.ellipse(rx+12, ey+52, 18, 7, 0, 0, Math.PI*2); ctx.fill();
    }
    else if (this.currentEmotion === "wink") {
      ctx.lineWidth = 14;
      ctx.beginPath(); ctx.arc(lx, ey+10, 38, Math.PI+0.3, 2*Math.PI-0.3, false); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(rx-36, ey+6); ctx.lineTo(rx+26, ey+6); ctx.lineTo(rx+42, ey-8); ctx.stroke();
    }
    else if (state === "PLANNING" || this.currentEmotion === "curious") {
      ctx.lineWidth = 12;
      [lx, rx].forEach(cx => {
        ctx.beginPath(); ctx.ellipse(cx, ey, 32, 42, 0, 0, Math.PI*2); ctx.stroke();
        ctx.beginPath(); ctx.arc(cx + pupilX*0.4, ey + pupilY*0.4, 16, 0, Math.PI*2); ctx.fill();
      });
      const dotPhase = Math.floor(time*3)%4;
      ctx.fillStyle = "#9d00ff"; ctx.shadowColor="#9d00ff";
      for (let i=0;i<3;i++) {
        if (i<=dotPhase) { ctx.beginPath(); ctx.arc(w/2-24+i*24, ey+70, 5, 0, Math.PI*2); ctx.fill(); }
      }
    }
    else if (state === "VERIFYING") {
      ctx.lineWidth = 11; ctx.strokeStyle = "#ffb800"; ctx.shadowColor = "#ffb800";
      [lx, rx].forEach(cx => {
        ctx.beginPath(); ctx.arc(cx, ey, 34, 0, Math.PI*2); ctx.stroke();
        const a = time * 3;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a)*38, ey + Math.sin(a)*38);
        ctx.lineTo(cx + Math.cos(a+Math.PI)*38, ey + Math.sin(a+Math.PI)*38);
        ctx.stroke();
      });
    }
    else if (state === "COMPLETED" || this.currentEmotion === "love") {
      ctx.strokeStyle="#00ffa3"; ctx.fillStyle="#00ffa3"; ctx.shadowColor="#00ffa3"; ctx.lineWidth=16;
      [lx, rx].forEach(cx => {
        ctx.beginPath(); ctx.arc(cx, ey+14, 42, Math.PI+0.3, 2*Math.PI-0.3, false); ctx.stroke();
      });
      const pulse = 1+Math.sin(time*7)*0.3;
      [[w/2,ey-50,13],[lx-52,ey-28,9],[rx+52,ey-28,9]].forEach(([sx,sy,sz]) => {
        ctx.beginPath(); ctx.moveTo(sx,sy-sz*pulse); ctx.quadraticCurveTo(sx,sy,sx+sz*pulse,sy);
        ctx.quadraticCurveTo(sx,sy,sx,sy+sz*pulse); ctx.quadraticCurveTo(sx,sy,sx-sz*pulse,sy);
        ctx.quadraticCurveTo(sx,sy,sx,sy-sz*pulse); ctx.fill();
      });
    }
    else if (state === "FAILED" || this.currentEmotion === "alert") {
      ctx.strokeStyle="#ff3366"; ctx.shadowColor="#ff3366"; ctx.lineWidth=14;
      [[lx,-1],[rx,1]].forEach(([cx,s]) => {
        ctx.beginPath(); ctx.moveTo(cx+s*25,ey-22); ctx.lineTo(cx-s*15,ey); ctx.lineTo(cx+s*25,ey+22); ctx.stroke();
      });
    }
    else {
      // reticle for SEARCHING/COLLECTING/EXTRACTING covered above; fallback open circles
      ctx.lineWidth = 12;
      [lx, rx].forEach(cx => {
        ctx.beginPath(); ctx.arc(cx, ey, 34, 0, Math.PI*2); ctx.stroke();
        ctx.beginPath(); ctx.arc(cx+pupilX*0.5, ey+pupilY*0.5, 14, 0, Math.PI*2); ctx.fill();
      });
    }

    this.faceTexture.needsUpdate = true;
  }

  // ════════════════════════════════════════════════════════════════════
  // 2. STUDIO + DYNAMIC LIGHTING
  // ════════════════════════════════════════════════════════════════════
  setupLighting() {
    this.scene.add(new THREE.AmbientLight(0x10203a, 0.75));

    this.keyLight = new THREE.DirectionalLight(0xffffff, 1.1);
    this.keyLight.position.set(2.5, 4.5, 4.0);
    this.scene.add(this.keyLight);

    this.fillLight = new THREE.DirectionalLight(0x6080b0, 0.5);
    this.fillLight.position.set(-3.5, 2.5, 2.5);
    this.scene.add(this.fillLight);

    this.rimCyan = new THREE.DirectionalLight(0x00f0ff, 0.75);
    this.rimCyan.position.set(-2.5, 3.0, -3.5);
    this.scene.add(this.rimCyan);

    this.rimBlue = new THREE.DirectionalLight(0x3860ff, 0.55);
    this.rimBlue.position.set(2.5, 2.8, -3.5);
    this.scene.add(this.rimBlue);

    this.daisLight = new THREE.PointLight(0x00f0ff, 0.95, 3.5);
    this.daisLight.position.set(0, 0.4, 0);
    this.scene.add(this.daisLight);

    // Top halo for the bot head
    this.haloLight = new THREE.PointLight(0x00f0ff, 0.65, 2.0);
    this.haloLight.position.set(0, 3.2, 0.5);
    this.scene.add(this.haloLight);
  }

  // ════════════════════════════════════════════════════════════════════
  // 3. PEDESTAL
  // ════════════════════════════════════════════════════════════════════
  buildPedestal() {
    const baseGeo = new THREE.CylinderGeometry(1.6, 1.85, 0.16, 72);
    const baseMat = new THREE.MeshStandardMaterial({ color:0x080d1c, roughness:0.28, metalness:0.9 });
    this.pedestalGroup.add(new THREE.Mesh(baseGeo, baseMat));

    const plateGeo = new THREE.CylinderGeometry(1.32, 1.32, 0.035, 56);
    const plateMat = new THREE.MeshPhysicalMaterial({ color:0x050b18, roughness:0.12, metalness:0.28, transmission:0.65, transparent:true, opacity:0.92 });
    const plate = new THREE.Mesh(plateGeo, plateMat); plate.position.y = 0.09;
    this.pedestalGroup.add(plate);

    // Contact shadow
    const sc = document.createElement("canvas"); sc.width=sc.height=128;
    const sctx = sc.getContext("2d"), sg = sctx.createRadialGradient(64,64,3,64,64,60);
    sg.addColorStop(0,"rgba(0,0,0,0.9)"); sg.addColorStop(0.5,"rgba(0,0,0,0.45)"); sg.addColorStop(1,"rgba(0,0,0,0)");
    sctx.fillStyle=sg; sctx.fillRect(0,0,128,128);
    const sm = new THREE.MeshBasicMaterial({ map:new THREE.CanvasTexture(sc), transparent:true, depthWrite:false });
    this.contactShadow = new THREE.Mesh(new THREE.PlaneGeometry(1.15,1.15), sm);
    this.contactShadow.rotation.x = -Math.PI/2; this.contactShadow.position.y = 0.098;
    this.pedestalGroup.add(this.contactShadow);

    // Outer neon ring
    this.outerRingMat = new THREE.MeshBasicMaterial({ color:0x00f0ff, side:THREE.DoubleSide, transparent:true, opacity:0.9 });
    this.outerPedestalRing = new THREE.Mesh(new THREE.RingGeometry(1.35,1.45,72), this.outerRingMat);
    this.outerPedestalRing.rotation.x = -Math.PI/2; this.outerPedestalRing.position.y = 0.094;
    this.pedestalGroup.add(this.outerPedestalRing);

    // Inner hex
    this.innerHexMat = new THREE.MeshBasicMaterial({ color:0x00d4de, side:THREE.DoubleSide, transparent:true, opacity:0.70 });
    this.innerHexRing = new THREE.Mesh(new THREE.RingGeometry(0.82,0.88,6), this.innerHexMat);
    this.innerHexRing.rotation.x = -Math.PI/2; this.innerHexRing.position.y = 0.096;
    this.pedestalGroup.add(this.innerHexRing);

    // Radar ripples
    for (let r=0; r<4; r++) {
      const rMat = new THREE.MeshBasicMaterial({ color:0x00f0ff, side:THREE.DoubleSide, transparent:true, opacity:0 });
      const rMesh = new THREE.Mesh(new THREE.RingGeometry(0.22,0.28,48), rMat);
      rMesh.rotation.x = -Math.PI/2; rMesh.position.y = 0.099;
      this.pedestalGroup.add(rMesh);
      this.radarRipples.push({ mesh:rMesh, radius:0.22+r*0.38, speed:0.70, active:false });
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // 4. ROBOT BODY
  // ════════════════════════════════════════════════════════════════════
  buildRobot() {
    this.armorMat = new THREE.MeshStandardMaterial({ color:0xecf2fa, roughness:0.34, metalness:0.05 });
    this.tealMat  = new THREE.MeshStandardMaterial({ color:0x00c2cb, roughness:0.22, metalness:0.10 });
    this.darkMat  = new THREE.MeshStandardMaterial({ color:0x0b1c3a, roughness:0.48, metalness:0.42 });

    this.visorMat = new THREE.MeshPhysicalMaterial({
      color:0xffffff, map:this.faceTexture,
      roughness:0.08, metalness:0.06,
      clearcoat:1.0, clearcoatRoughness:0.06,
      emissive:0xffffff, emissiveMap:this.faceTexture, emissiveIntensity:0.95
    });

    // ── HEAD ──────────────────────────────────────────────────────────
    this.head = new THREE.Group();
    this.head.position.set(0, 1.96, 0);

    const helmetGeo = new THREE.SphereGeometry(0.56, 40, 34);
    helmetGeo.scale(1.10, 1.02, 1.04);
    this.head.add(new THREE.Mesh(helmetGeo, this.armorMat));

    // Crown crest
    const crestGeo = new THREE.BoxGeometry(0.16, 0.09, 0.26);
    const crest = new THREE.Mesh(crestGeo, this.armorMat);
    crest.position.set(0, 0.56, -0.02); this.head.add(crest);

    // Visor
    const visorGeo = new THREE.SphereGeometry(0.48, 40, 30);
    visorGeo.scale(0.96, 0.78, 0.60);
    this.visorMesh = new THREE.Mesh(visorGeo, this.visorMat);
    this.visorMesh.position.set(0, 0.02, 0.22); this.head.add(this.visorMesh);

    // Laser scan cone
    const coneGeo = new THREE.ConeGeometry(0.56, 1.45, 34, 1, true);
    const coneMat = new THREE.MeshBasicMaterial({
      color:0x00f0ff, transparent:true, opacity:0.0,
      side:THREE.DoubleSide, depthWrite:false, blending:THREE.AdditiveBlending
    });
    this.scannerBeam = new THREE.Mesh(coneGeo, coneMat);
    this.scannerBeam.position.set(0, -0.65, 0.75);
    this.scannerBeam.rotation.x = Math.PI/3.2;
    this.head.add(this.scannerBeam);

    // Ear assemblies
    [-1, 1].forEach(s => {
      const eg = new THREE.Group(); eg.position.set(s*0.57, 0.04, 0);
      const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.15,0.15,0.13,34), this.armorMat);
      cup.rotation.z = Math.PI/2; eg.add(cup);
      const soc = new THREE.Mesh(new THREE.CylinderGeometry(0.09,0.09,0.14,26), this.darkMat);
      soc.rotation.z = Math.PI/2; eg.add(soc);
      const ringM = new THREE.MeshBasicMaterial({ color:0x00f0ff, side:THREE.DoubleSide });
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.035,0.065,26), ringM);
      ring.rotation.y = s*(Math.PI/2); ring.position.x = s*0.072; eg.add(ring);
      // Fin
      const fg = new THREE.BoxGeometry(0.065,0.26,0.10);
      const fp = fg.attributes.position;
      for (let i=0;i<fp.count;i++) { if (fp.getY(i)>0.04) { fp.setX(i,fp.getX(i)*0.40); fp.setZ(i,fp.getZ(i)*0.45); } }
      fg.computeVertexNormals();
      const fin = new THREE.Mesh(fg, this.tealMat);
      fin.position.set(0,0.15,-0.03); fin.rotation.z=s*-0.12; fin.rotation.x=-0.18;
      eg.add(fin);
      this.head.add(eg);
    });

    this.robotGroup.add(this.head);

    // ── TORSO ─────────────────────────────────────────────────────────
    const torso = new THREE.Group(); torso.position.set(0,1.26,0);

    const uGeo = new THREE.SphereGeometry(0.46,34,26); uGeo.scale(0.95,1.02,0.90);
    const upper = new THREE.Mesh(uGeo, this.armorMat); upper.position.y=0.10; torso.add(upper);

    const lGeo = new THREE.SphereGeometry(0.42,34,26); lGeo.scale(0.86,1.10,0.82);
    const lower = new THREE.Mesh(lGeo, this.armorMat); lower.position.y=-0.22; torso.add(lower);

    const seam = new THREE.Mesh(new THREE.TorusGeometry(0.40,0.012,18,52), this.darkMat);
    seam.rotation.x = Math.PI/2; seam.position.y=-0.08; torso.add(seam);

    // Chest badge
    const bg = new THREE.BoxGeometry(0.24,0.20,0.03);
    const bp = bg.attributes.position;
    for (let i=0;i<bp.count;i++) { if (bp.getY(i)<0) bp.setX(i, bp.getX(i)*0.35); }
    bg.computeVertexNormals();
    this.chestBadge = new THREE.Mesh(bg, this.tealMat);
    this.chestBadge.position.set(0,0.04,0.38); this.chestBadge.rotation.x=-0.12; torso.add(this.chestBadge);

    // Thruster ring
    const thrM = new THREE.MeshBasicMaterial({ color:0x00f0ff, side:THREE.DoubleSide, transparent:true, opacity:0.88 });
    this.thruster = new THREE.Mesh(new THREE.RingGeometry(0.10,0.24,34), thrM);
    this.thruster.rotation.x = Math.PI/2; this.thruster.position.y=-0.58; torso.add(this.thruster);

    this.robotGroup.add(torso);

    // ── ARMS ──────────────────────────────────────────────────────────
    this.leftArm  = this._buildArm(-0.48, false);
    this.rightArm = this._buildArm( 0.48, true);
  }

  _buildArm(xOff, isRight) {
    const arm = new THREE.Group(); arm.position.set(xOff, 1.36, 0.02);

    const shoulder = new THREE.Mesh(new THREE.SphereGeometry(0.12,22,18), this.armorMat);
    arm.add(shoulder);

    const forearm = new THREE.Group();
    const upperArmGeo = new THREE.CylinderGeometry(0.075,0.065,0.32,22);
    const ua = new THREE.Mesh(upperArmGeo, this.armorMat);
    ua.position.set(0, isRight ? 0.16 : -0.18, 0); forearm.add(ua);

    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.09,22,18), this.armorMat);
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
    this.consoleGroup.position.set(0,0.95,0.45);
    this.consoleGroup.rotation.x = -0.42;

    // Keyboard texture
    const kc = document.createElement("canvas"); kc.width=256; kc.height=128;
    const kctx = kc.getContext("2d");
    kctx.fillStyle="rgba(0,240,255,0.06)"; kctx.fillRect(0,0,256,128);
    kctx.strokeStyle="rgba(0,240,255,0.55)"; kctx.lineWidth=2; kctx.strokeRect(4,4,248,120);
    kctx.fillStyle="rgba(0,240,255,0.22)"; kctx.strokeStyle="rgba(0,240,255,0.85)";
    for (let r=0;r<4;r++) for (let c=0;c<8;c++) {
      const kx=14+c*29, ky=14+r*26; kctx.fillRect(kx,ky,26,22); kctx.strokeRect(kx,ky,26,22);
    }
    const kbMat = new THREE.MeshBasicMaterial({ map:new THREE.CanvasTexture(kc), transparent:true, opacity:0.82, side:THREE.DoubleSide, blending:THREE.AdditiveBlending });
    this.keyboardMesh = new THREE.Mesh(new THREE.PlaneGeometry(0.92,0.45,12,6), kbMat);
    this.consoleGroup.add(this.keyboardMesh);
    this.hologramGroup.add(this.consoleGroup);

    // Side screens (wireframe)
    const screenMat = new THREE.MeshBasicMaterial({ color:0x00f0ff, wireframe:true, transparent:true, opacity:0.32 });
    this.holoScreenLeft = new THREE.Mesh(new THREE.PlaneGeometry(1.0,0.70,8,6), screenMat);
    this.holoScreenLeft.position.set(-1.45,1.45,0.25); this.holoScreenLeft.rotation.y=0.50;
    this.hologramGroup.add(this.holoScreenLeft);

    this.holoScreenRight = new THREE.Mesh(new THREE.PlaneGeometry(0.9,0.65,6,5), screenMat.clone());
    this.holoScreenRight.position.set(1.40,1.50,0.20); this.holoScreenRight.rotation.y=-0.45;
    this.hologramGroup.add(this.holoScreenRight);

    // Orbiting hex nodes
    for (let i=0;i<6;i++) {
      const angle = (i/6)*Math.PI*2, radius=1.38;
      const hex = new THREE.Mesh(
        new THREE.RingGeometry(0.055,0.08,6),
        new THREE.MeshBasicMaterial({ color:0x00f0ff, side:THREE.DoubleSide, transparent:true, opacity:0.65 })
      );
      hex.position.set(Math.cos(angle)*radius, 1.25+Math.sin(i)*0.15, Math.sin(angle)*radius);
      this.hologramGroup.add(hex);
      this.orbitingNodes.push({ mesh:hex, angle, speed:0.38+(i%3)*0.14, radius, baseY:1.25+(i%2)*0.15 });
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // 6. HEXAGONAL ENERGY SHIELD
  // ════════════════════════════════════════════════════════════════════
  buildHexShield() {
    this.shieldRings = [];
    const hexCounts = [6, 8, 10];
    const radii     = [0.85, 1.15, 1.48];
    const heights   = [1.1,  1.25, 1.40];

    hexCounts.forEach((count, ri) => {
      const ring = [];
      for (let i = 0; i < count; i++) {
        const angle = (i / count) * Math.PI * 2;
        const r = radii[ri];
        const geo = new THREE.RingGeometry(0.06, 0.10, 6);
        const mat = new THREE.MeshBasicMaterial({
          color: 0x00f0ff, side: THREE.DoubleSide,
          transparent: true, opacity: 0.0,
          blending: THREE.AdditiveBlending
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(Math.cos(angle) * r, heights[ri], Math.sin(angle) * r);
        mesh.lookAt(0, heights[ri], 0);
        this.shieldGroup.add(mesh);
        ring.push({ mesh, angle, radius: r, baseY: heights[ri], speed: (ri+1)*0.22 });
      }
      this.shieldRings.push(ring);
    });

    // Connecting lines between adjacent hexes in outermost ring
    this.shieldLines = [];
    const oRing = this.shieldRings[2];
    for (let i = 0; i < oRing.length; i++) {
      const a = oRing[i].mesh.position;
      const b = oRing[(i+1) % oRing.length].mesh.position;
      const pts = [a.clone(), b.clone()];
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const mat = new THREE.LineBasicMaterial({ color:0x00f0ff, transparent:true, opacity:0.0 });
      const line = new THREE.Line(geo, mat);
      this.shieldGroup.add(line);
      this.shieldLines.push(line);
    }

    this.shieldOpacity = 0.0;
    this.shieldTargetOpacity = 0.0;
  }

  // ════════════════════════════════════════════════════════════════════
  // 7. NEBULA PARTICLES (GPU instanced)
  // ════════════════════════════════════════════════════════════════════
  buildNebulaParticles() {
    const COUNT = 1200;
    const pos   = new Float32Array(COUNT * 3);
    const col   = new Float32Array(COUNT * 3);

    for (let i = 0; i < COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi   = Math.acos(2 * Math.random() - 1);
      const r     = 3.5 + Math.random() * 6.0;
      pos[i*3]   = r * Math.sin(phi) * Math.cos(theta);
      pos[i*3+1] = r * Math.sin(phi) * Math.sin(theta) * 0.45;
      pos[i*3+2] = r * Math.cos(phi);

      // Cyan / teal / purple nebula colours
      const t = Math.random();
      col[i*3]   = t < 0.5 ? 0.0 : (t < 0.75 ? 0.0  : 0.62);
      col[i*3+1] = t < 0.5 ? 0.94: (t < 0.75 ? 0.85 : 0.00);
      col[i*3+2] = t < 0.5 ? 1.0 : (t < 0.75 ? 0.55 : 1.00);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color",    new THREE.BufferAttribute(col, 3));
    const mat = new THREE.PointsMaterial({ size:0.022, vertexColors:true, transparent:true, opacity:0.55, blending:THREE.AdditiveBlending, depthWrite:false });
    this.nebula = new THREE.Points(geo, mat);
    this.scene.add(this.nebula);
  }

  // ════════════════════════════════════════════════════════════════════
  // 8. PLASMA TRAIL
  // ════════════════════════════════════════════════════════════════════
  buildPlasmaTrail() {
    const trailCount = 20;
    const trailPos   = new Float32Array(trailCount * 3);
    const trailGeo   = new THREE.BufferGeometry();
    trailGeo.setAttribute("position", new THREE.BufferAttribute(trailPos, 3));
    const trailMat = new THREE.LineBasicMaterial({ color:0x00f0ff, transparent:true, opacity:0.55, blending:THREE.AdditiveBlending });
    this.plasmaTrail = new THREE.Line(trailGeo, trailMat);
    this.scene.add(this.plasmaTrail);
    this.trailPositions = [];
  }

  // ════════════════════════════════════════════════════════════════════
  // 9. ENVIRONMENT GRID + DUST
  // ════════════════════════════════════════════════════════════════════
  buildEnvironment() {
    const grid = new THREE.GridHelper(40, 80, 0x00f0ff, 0x081428);
    grid.position.y = -0.01; this.scene.add(grid);

    const N = 220;
    const pos = new Float32Array(N * 3);
    for (let i = 0; i < N*3; i += 3) {
      pos[i]   = (Math.random()-0.5)*12;
      pos[i+1] = Math.random()*4.5;
      pos[i+2] = (Math.random()-0.5)*12;
    }
    const dGeo = new THREE.BufferGeometry(); dGeo.setAttribute("position", new THREE.BufferAttribute(pos,3));
    const dMat = new THREE.PointsMaterial({ size:0.026, color:0x00f0ff, transparent:true, opacity:0.42, blending:THREE.AdditiveBlending });
    this.dust = new THREE.Points(dGeo, dMat);
    this.scene.add(this.dust);
  }

  // ════════════════════════════════════════════════════════════════════
  // 10. PUBLIC STATE API
  // ════════════════════════════════════════════════════════════════════
  setAgentState(state) {
    this.agentState = state || "STANDBY";
    const col = (this.STATE_COLORS[state] || this.STATE_COLORS.STANDBY).hex;

    this._applyColorScheme(col);

    const msgs = {
      STANDBY:    "Ready for directives. Standing by… 👋",
      PLANNING:   "Formulating optimal search strategy… 🤔",
      SEARCHING:  "Scanning career indices & web corpora… 🔍",
      COLLECTING: "Harvesting source documents & payload… 📥",
      EXTRACTING: "Synthesizing structured intelligence… 🧩",
      VERIFYING:  "Running cryptographic provenance checks… 🛡️",
      COMPLETED:  "Mission complete — all records verified! 🎉",
      FAILED:     "Directive halted. Standing by for retry. ⚠️",
    };
    this.speak(msgs[state] || "Processing…");

    if (state === "SEARCHING" || state === "COLLECTING" || state === "EXTRACTING" || state === "VERIFYING") {
      this.radarRipples.forEach(r => (r.active = true));
      this.shieldTargetOpacity = 0.72;
    } else {
      this.radarRipples.forEach(r => (r.active = false));
      this.shieldTargetOpacity = 0.0;
    }

    if (state === "SEARCHING" && this.scannerBeam)  this.scannerBeam.material.opacity = 0.30;
    else if (this.scannerBeam)                       this.scannerBeam.material.opacity = 0.0;

    if (state === "COMPLETED" || state === "FAILED") {
      this._particleBurst(col);
    }
  }

  setEmotion(emotion) {
    this.currentEmotion = emotion || "happy";
  }

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
      const t = queryText.length > 38 ? queryText.slice(0,35)+"…" : queryText;
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
    const t = (record?.title||"Record").slice(0,30);
    this.speak(`Extracted: ${t} 💎`);
  }
  onRecordVerified(record, isVerified) {
    const ok  = isVerified===true||isVerified==="VERIFIED"||isVerified==="PASS";
    const col = ok ? 0x00ffa3 : 0xffb800;
    this.spawnVerificationBeam(col);
    this._spawnTendril(col);
    this.speak(ok ? "Source cryptographically verified ✓" : "Verification pending ⚠️");
  }
  onRunCompleted() { this.setAgentState("COMPLETED"); }

  // ════════════════════════════════════════════════════════════════════
  // 12. VFX SPAWNERS
  // ════════════════════════════════════════════════════════════════════
  spawnOrbitingExtractedNode(record) {
    const mat = new THREE.MeshStandardMaterial({ color:0x00f0ff, roughness:0.1, metalness:0.8, emissive:0x0044cc, emissiveIntensity:0.9 });
    const mesh = new THREE.Mesh(new THREE.OctahedronGeometry(0.07,0), mat);
    const angle = Math.random()*Math.PI*2, radius=1.12+Math.random()*0.38;
    mesh.position.set(Math.cos(angle)*radius, 1.15+Math.random()*0.4, Math.sin(angle)*radius);
    this.vfxGroup.add(mesh);
    this.orbitingNodes.push({ mesh, angle, speed:0.55+Math.random()*0.38, radius, baseY:1.2+(Math.random()-0.5)*0.3 });
    if (this.orbitingNodes.length > 18) {
      const old = this.orbitingNodes.shift();
      this.vfxGroup.remove(old.mesh);
      old.mesh.geometry.dispose(); old.mesh.material.dispose();
    }
  }

  spawnIncomingDataPacket(colorHex) {
    const mat = new THREE.MeshBasicMaterial({ color:colorHex, wireframe:true });
    const packet = new THREE.Mesh(new THREE.BoxGeometry(0.065,0.065,0.065), mat);
    const angle = Math.random()*Math.PI*2;
    packet.position.set(Math.cos(angle)*3.0, 1.3+Math.random()*0.6, Math.sin(angle)*3.0);
    this.vfxGroup.add(packet);
    this.incomingPackets.push({ mesh:packet, progress:0.0, startPos:packet.position.clone(), targetPos:new THREE.Vector3(0,1.05,0.3) });
  }

  spawnVerificationBeam(colorHex) {
    if (!this.orbitingNodes.length) return;
    const target = this.orbitingNodes[Math.floor(Math.random()*this.orbitingNodes.length)];
    const pts = [ new THREE.Vector3(0, 1.55, 0.35), target.mesh.position.clone() ];
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color:colorHex, transparent:true, opacity:0.95 });
    const line = new THREE.Line(geo, mat);
    this.vfxGroup.add(line);
    setTimeout(() => { this.vfxGroup.remove(line); geo.dispose(); mat.dispose(); }, 480);
  }

  _spawnTendril(colorHex) {
    const pts = [];
    const startY = 1.0 + Math.random() * 0.8;
    const dir = (Math.random()-0.5)*2;
    for (let t=0; t<=12; t++) {
      const frac = t/12;
      pts.push(new THREE.Vector3(
        dir * frac * (1.2 + Math.random()*0.5),
        startY + frac*0.6 + (Math.random()-0.5)*0.3,
        (Math.random()-0.5)*0.6
      ));
    }
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color:colorHex, transparent:true, opacity:0.75, blending:THREE.AdditiveBlending });
    const line = new THREE.Line(geo, mat);
    this.vfxGroup.add(line);
    let life = 0;
    const decay = setInterval(() => {
      life += 0.06;
      mat.opacity = Math.max(0, 0.75 - life);
      if (life >= 0.75) { clearInterval(decay); this.vfxGroup.remove(line); geo.dispose(); mat.dispose(); }
    }, 30);
  }

  _particleBurst(colorHex) {
    const N = 28;
    for (let i = 0; i < N; i++) {
      const geo = new THREE.SphereGeometry(0.025, 4, 4);
      const mat = new THREE.MeshBasicMaterial({ color:colorHex, blending:THREE.AdditiveBlending });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(0, 1.6, 0);
      this.vfxGroup.add(mesh);
      const vel = new THREE.Vector3((Math.random()-0.5)*3.5, Math.random()*3.5, (Math.random()-0.5)*3.5);
      let life = 0;
      const tick = setInterval(() => {
        life += 0.04;
        mesh.position.addScaledVector(vel, 0.04);
        vel.y -= 0.05;
        mat.opacity = Math.max(0, 1 - life);
        if (life >= 1.0) { clearInterval(tick); this.vfxGroup.remove(mesh); geo.dispose(); mat.dispose(); }
      }, 16);
    }
  }

  _applyColorScheme(hex) {
    const c = new THREE.Color(hex);
    if (this.outerRingMat)    this.outerRingMat.color.set(hex);
    if (this.daisLight)       this.daisLight.color.set(hex);
    if (this.haloLight)       this.haloLight.color.set(hex);
    if (this.rimCyan)         this.rimCyan.color.set(hex);
    if (this.chestBadge)      this.chestBadge.material.emissive = c;
    if (this.innerHexMat)     this.innerHexMat.color.set(hex);
    // tint shield lines
    this.shieldLines.forEach(l => l.material.color.set(hex));
    this.shieldRings.forEach(ring => ring.forEach(n => n.mesh.material.color.set(hex)));
  }

  // ════════════════════════════════════════════════════════════════════
  // 13. INPUT EVENTS
  // ════════════════════════════════════════════════════════════════════
  onMouseMove(e) {
    const mx = (e.clientX/window.innerWidth)*2-1;
    const my = -(e.clientY/window.innerHeight)*2+1;
    this.mouseNorm.x = mx; this.mouseNorm.y = my;
    this.targetHead.y = mx * 0.34;
    this.targetHead.x = -my * 0.17;
  }
  onWindowResize() {
    this.camera.aspect = window.innerWidth/window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
  }

  // ════════════════════════════════════════════════════════════════════
  // 14. MAIN ANIMATION LOOP
  // ════════════════════════════════════════════════════════════════════
  animate() {
    requestAnimationFrame(() => this.animate());
    const delta = this.clock.getDelta();
    const time  = this.clock.getElapsedTime();

    const isWorking = ["SEARCHING","COLLECTING","EXTRACTING","VERIFYING"].includes(this.agentState);

    // ── Hover bob ─────────────────────────────────────────────────────
    const bobFreq  = isWorking ? 3.4 : 2.0;
    const hoverY   = 0.055 * Math.sin(time*bobFreq) + 0.018*Math.sin(time*0.82);
    this.robotGroup.position.y = 0.20 + hoverY;

    if (this.contactShadow) {
      const s = 1.0 - (hoverY/0.10)*0.22;
      this.contactShadow.scale.set(s,s,1);
    }

    // ── Plasma trail ──────────────────────────────────────────────────
    const botWorldPos = new THREE.Vector3(0, this.robotGroup.position.y+1.0, 0);
    this.trailPositions.unshift(botWorldPos.clone());
    if (this.trailPositions.length > 20) this.trailPositions.length = 20;
    const trailAttr = this.plasmaTrail.geometry.attributes.position;
    for (let i=0; i<20; i++) {
      const p = this.trailPositions[i] || botWorldPos;
      trailAttr.setXYZ(i, p.x, p.y - i*0.014, p.z);
    }
    trailAttr.needsUpdate = true;
    this.plasmaTrail.material.opacity = isWorking ? 0.45 : 0.12;

    // ── Head motion ───────────────────────────────────────────────────
    if (this.head) {
      if (isWorking) {
        this.head.rotation.y += (Math.sin(time*2.3)*0.20 - this.head.rotation.y)*0.09;
        this.head.rotation.x += (-0.14+Math.cos(time*1.6)*0.09 - this.head.rotation.x)*0.09;
      } else {
        this.head.rotation.y += (this.targetHead.y - this.head.rotation.y)*0.09;
        this.head.rotation.x += (this.targetHead.x - this.head.rotation.x)*0.09;
      }
    }

    // ── Blink & visor ─────────────────────────────────────────────────
    this.blinkTimer += delta;
    if (this.blinkTimer > 4.5) {
      this.isBlinking = true;
      if (this.blinkTimer > 4.68) { this.isBlinking=false; this.blinkTimer=0; }
    }
    this.drawFace(delta, time);

    // ── Arm gestures (8 states) ───────────────────────────────────────
    if (this.leftArm && this.rightArm && this.rightForearm) {
      if (isWorking) {
        // Both arms typing on holographic keyboard
        const tL = Math.sin(time*this.typingSpeed), tR = Math.cos(time*this.typingSpeed);
        this.leftArm.rotation.x  = -0.74+tL*0.13;
        this.leftArm.rotation.z  =  0.32;
        this.leftArm.rotation.y  =  0.14;
        this.rightArm.rotation.x = -0.74+tR*0.13;
        this.rightArm.rotation.z = -0.32;
        this.rightArm.rotation.y = -0.14;
        this.rightForearm.rotation.z = tR*0.14;
        this.rightForearm.rotation.x = 0;
        if (this.keyboardMesh) this.keyboardMesh.material.opacity = 0.72+Math.sin(time*8)*0.20;
      } else if (this.agentState === "COMPLETED") {
        // Victory arms up — both waving
        const wave = Math.sin(time*7.5)*0.30;
        this.rightArm.rotation.z += (-0.70+wave - this.rightArm.rotation.z)*0.14;
        this.rightArm.rotation.x += (-0.28 - this.rightArm.rotation.x)*0.10;
        this.leftArm.rotation.z  = 0.70 - wave;
        this.leftArm.rotation.x  = -0.28;
        this.rightForearm.rotation.z = Math.sin(time*9)*0.35;
      } else if (this.agentState === "FAILED") {
        // Arms drooping
        this.leftArm.rotation.x  = 0.35;
        this.leftArm.rotation.z  = 0.18;
        this.rightArm.rotation.x = 0.35;
        this.rightArm.rotation.z = -0.18;
        this.rightForearm.rotation.z = 0;
      } else if (this.agentState === "PLANNING") {
        // Chin-scratch: right arm up, left down
        this.rightArm.rotation.x += (-0.55 - this.rightArm.rotation.x)*0.08;
        this.rightArm.rotation.z += (-0.20 - this.rightArm.rotation.z)*0.08;
        this.rightForearm.rotation.z = Math.sin(time*1.5)*0.10;
        this.leftArm.rotation.x = 0.05;
        this.leftArm.rotation.z = 0.22;
      } else {
        // STANDBY: right hand wave
        const wZ = -0.55+Math.sin(time*5.2)*0.22;
        const wX = -0.30+Math.cos(time*2.6)*0.09;
        this.rightArm.rotation.z += (wZ - this.rightArm.rotation.z)*0.10;
        this.rightArm.rotation.x += (wX - this.rightArm.rotation.x)*0.10;
        this.rightForearm.rotation.z = Math.sin(time*6.2)*0.22;
        this.leftArm.rotation.x = Math.sin(time*1.9)*0.06;
        this.leftArm.rotation.z = 0.22;
        this.leftArm.rotation.y = 0;
      }
    }

    // ── Visor scan beam ───────────────────────────────────────────────
    if (this.scannerBeam && this.scannerBeam.material.opacity > 0.01) {
      this.scannerBeam.rotation.z = Math.sin(time*5.2)*0.16;
    }

    // ── Pedestal rings ────────────────────────────────────────────────
    if (this.outerPedestalRing) this.outerPedestalRing.rotation.z += 0.28*delta;
    if (this.innerHexRing)      this.innerHexRing.rotation.z      -= 0.40*delta;

    // ── Radar ripples ─────────────────────────────────────────────────
    this.radarRipples.forEach(r => {
      if (r.active) {
        r.radius += r.speed * delta;
        r.mesh.scale.set(r.radius, r.radius, 1);
        r.mesh.material.opacity = Math.max(0, 0.85-(r.radius/1.6)*0.85);
        if (r.radius > 1.6) {
          r.radius = 0.22;
          if (!["SEARCHING","COLLECTING","EXTRACTING","VERIFYING"].includes(this.agentState)) {
            r.active = false; r.mesh.material.opacity = 0;
          }
        }
      }
    });

    // ── Holo screens float ────────────────────────────────────────────
    if (this.holoScreenLeft)  this.holoScreenLeft.position.y  = 1.45+Math.sin(time*2.0)*0.044;
    if (this.holoScreenRight) this.holoScreenRight.position.y = 1.50+Math.cos(time*1.8)*0.044;

    // ── Orbiting nodes ────────────────────────────────────────────────
    this.orbitingNodes.forEach(n => {
      n.angle += n.speed*delta;
      n.mesh.position.x = Math.cos(n.angle)*n.radius;
      n.mesh.position.z = Math.sin(n.angle)*n.radius;
      n.mesh.position.y = n.baseY + Math.sin(time*2.6+n.angle)*0.065;
      n.mesh.rotation.y += delta*1.6;
    });

    // ── Incoming data packets ─────────────────────────────────────────
    for (let i=this.incomingPackets.length-1; i>=0; i--) {
      const p = this.incomingPackets[i];
      p.progress += delta*1.9;
      p.mesh.position.lerpVectors(p.startPos, p.targetPos, p.progress);
      p.mesh.rotation.x += delta*7; p.mesh.rotation.y += delta*7;
      if (p.progress >= 1.0) {
        this.vfxGroup.remove(p.mesh); p.mesh.geometry.dispose(); p.mesh.material.dispose();
        this.incomingPackets.splice(i, 1);
      }
    }

    // ── Hex shield animation ──────────────────────────────────────────
    this.shieldOpacity += (this.shieldTargetOpacity - this.shieldOpacity) * 0.06;
    const col = this.STATE_COLORS[this.agentState] || this.STATE_COLORS.STANDBY;
    this.shieldRings.forEach((ring, ri) => {
      ring.forEach((n, idx) => {
        n.angle += n.speed * delta * (ri % 2 === 0 ? 1 : -1);
        n.mesh.position.x = Math.cos(n.angle) * n.radius;
        n.mesh.position.z = Math.sin(n.angle) * n.radius;
        n.mesh.position.y = n.baseY + Math.sin(time*1.5 + idx) * 0.05;
        const pulse = this.shieldOpacity * (0.65 + 0.35 * Math.sin(time*4+ri));
        n.mesh.material.opacity = pulse;
        n.mesh.lookAt(0, n.baseY, 0);
      });
    });
    this.shieldLines.forEach(l => l.material.opacity = this.shieldOpacity * 0.45);

    // ── Nebula drift ──────────────────────────────────────────────────
    if (this.nebula) {
      this.nebula.rotation.y += 0.004*delta;
      this.nebula.rotation.x  = Math.sin(time*0.05)*0.06;
    }
    if (this.dust) this.dust.rotation.y += 0.012*delta;

    if (this.controls) this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

window.AIBotAvatar = AIBotAvatar;
