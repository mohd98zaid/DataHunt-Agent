// Three.js Cybernetic Chibi Robot Avatar & Real-Time Backend Visualizer
// Precisely modeled after user reference image (media_1788928457114.png):
// - Soft satin white chibi spherical head with crown bump
// - Deep dark navy glossy visor screen
// - Crisp, bright glowing cyan smiling curved eyes (^ ^)
// - White headphone earcups with vibrant TEAL/CYAN upward antenna fins
// - Chubby floating capsule/egg body (NO LEGS!) with dark seam groove
// - Vibrant teal chest shield badge and soft hover thruster glow
// - Articulated stubby arms with waving hello gesture (👋)
// - Balanced studio lighting and calibrated bloom for crisp, beautiful 3D shading

class CyberneticAgentScene {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.composer = null;
    this.controls = null;

    if (!this.canvas || typeof THREE === 'undefined') {
      console.warn("[CyberneticScene] Three.js or canvas element not available");
      return;
    }

    this.clock = new THREE.Clock();

    // Scene Groups
    this.robotGroup = new THREE.Group();
    this.pedestalGroup = new THREE.Group();
    this.hologramGroup = new THREE.Group();
    this.orbitingNodesGroup = new THREE.Group();
    this.effectsGroup = new THREE.Group();

    // Robot Anatomy
    this.head = null;
    this.visorMesh = null;
    this.eyeGroup = new THREE.Group();
    this.leftEye = null;
    this.rightEye = null;
    this.scanLaserBar = null;
    this.leftArm = null;
    this.rightArm = null;
    this.rightForearm = null;
    this.chestBadge = null;
    this.hoverLight = null;

    // Pedestal
    this.pedestalRing = null;
    this.hexRing = null;
    this.radarRipples = [];

    // Holographic Displays
    this.holoScreenLeft = null;
    this.holoScreenRight = null;
    this.orbitingNodes = [];
    this.incomingPackets = [];

    // State & Animation
    this.agentState = "STANDBY";
    this.targetHeadRotation = { x: 0, y: 0 };
    this.isBlinking = false;
    this.blinkTimer = 0;

    try {
      this.init();
    } catch (err) {
      console.error("[CyberneticScene] Error initializing:", err);
    }
  }

  init() {
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x070b16, 0.035);

    const aspect = window.innerWidth / window.innerHeight;
    this.camera = new THREE.PerspectiveCamera(38, aspect, 0.1, 100);
    // Position camera closer to clearly focus on the cute chibi robot
    this.camera.position.set(0, 1.65, 4.4);

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      powerPreference: "high-performance",
      alpha: true
    });
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;

    if (typeof THREE.OrbitControls !== 'undefined') {
      this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.05;
      this.controls.maxPolarAngle = Math.PI / 2 - 0.05; // Stay above dais
      this.controls.minDistance = 2.8;
      this.controls.maxDistance = 7.5;
      this.controls.target.set(0, 1.35, 0);
    }

    this.setupLighting();
    this.buildPedestal();
    this.buildChibiRobot();
    this.buildFloatingHolograms();
    this.buildEnvironment();

    this.scene.add(this.pedestalGroup);
    this.scene.add(this.robotGroup);
    this.scene.add(this.hologramGroup);
    this.scene.add(this.orbitingNodesGroup);
    this.scene.add(this.effectsGroup);

    this.setupPostprocessing();

    window.addEventListener("resize", () => this.onWindowResize());
    window.addEventListener("mousemove", (e) => this.onMouseMove(e));

    this.animate();
  }

  setupLighting() {
    // Balanced, soft studio lighting (soft diffuse illumination, NO blinding overblown bloom)
    const ambient = new THREE.AmbientLight(0x1a2638, 0.70);
    this.scene.add(ambient);

    // Front Key Light: soft white daylight illumination
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
    keyLight.position.set(2.5, 3.5, 4.0);
    this.scene.add(keyLight);

    // Soft Fill Light from opposite angle to soften shadows
    const fillLight = new THREE.DirectionalLight(0x7090b8, 0.45);
    fillLight.position.set(-3.0, 2.0, 2.5);
    this.scene.add(fillLight);

    // Subtle Cyan Rim Light from behind to define the silhouette
    const cyanRim = new THREE.DirectionalLight(0x00f0ff, 0.65);
    cyanRim.position.set(-2.2, 3.0, -3.5);
    this.scene.add(cyanRim);

    // Subtle Cool Blue back rim
    const backRim = new THREE.DirectionalLight(0x4060ff, 0.50);
    backRim.position.set(2.5, 2.5, -3.5);
    this.scene.add(backRim);

    // Gentle dais upward glow (subtle, NOT blinding)
    this.hoverLight = new THREE.PointLight(0x00f0ff, 0.85, 3.0);
    this.hoverLight.position.set(0, 0.4, 0);
    this.scene.add(this.hoverLight);
  }

  buildPedestal() {
    // Sleek high-tech metallic platform with glowing cyan concentric rings
    const daisGeo = new THREE.CylinderGeometry(2.0, 2.2, 0.16, 64);
    const daisMat = new THREE.MeshStandardMaterial({
      color: 0x090f1f,
      roughness: 0.3,
      metalness: 0.8
    });
    const dais = new THREE.Mesh(daisGeo, daisMat);
    dais.position.y = 0;
    this.pedestalGroup.add(dais);

    // Inner Glass Plate
    const plateGeo = new THREE.CylinderGeometry(1.7, 1.7, 0.03, 48);
    const plateMat = new THREE.MeshPhysicalMaterial({
      color: 0x050a16,
      roughness: 0.15,
      metalness: 0.3,
      transmission: 0.5,
      transparent: true,
      opacity: 0.90
    });
    const plate = new THREE.Mesh(plateGeo, plateMat);
    plate.position.y = 0.09;
    this.pedestalGroup.add(plate);

    // Primary Neon Cyan Outer Ring
    const ringGeo = new THREE.RingGeometry(1.72, 1.82, 64);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0x00f0ff,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85
    });
    this.pedestalRing = new THREE.Mesh(ringGeo, ringMat);
    this.pedestalRing.rotation.x = -Math.PI / 2;
    this.pedestalRing.position.y = 0.10;
    this.pedestalGroup.add(this.pedestalRing);

    // Rotating Inner Hexagonal Rune Ring
    const hexGeo = new THREE.RingGeometry(1.18, 1.25, 6);
    const hexMat = new THREE.MeshBasicMaterial({
      color: 0x00d4de,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.65
    });
    this.hexRing = new THREE.Mesh(hexGeo, hexMat);
    this.hexRing.rotation.x = -Math.PI / 2;
    this.hexRing.position.y = 0.105;
    this.pedestalGroup.add(this.hexRing);

    // Expanding Radar Ripples on Dais (active during SEARCHING)
    for (let r = 0; r < 3; r++) {
      const rippleGeo = new THREE.RingGeometry(0.3, 0.34, 48);
      const rippleMat = new THREE.MeshBasicMaterial({
        color: 0x00f0ff,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.0
      });
      const ripple = new THREE.Mesh(rippleGeo, rippleMat);
      ripple.rotation.x = -Math.PI / 2;
      ripple.position.y = 0.108;
      this.pedestalGroup.add(ripple);
      this.radarRipples.push({
        mesh: ripple,
        radius: 0.3 + r * 0.45,
        speed: 0.7,
        active: false
      });
    }
  }

  // 3D CHIBI ROBOT MATCHING REFERENCE IMAGE (media_1788928457114.png)
  buildChibiRobot() {
    // 1. Materials (Crisp, soft satin toy finish matching reference image media_1788928457114.png)
    // Soft satin porcelain white armor (matte/satin Pixar toy look - NO overblown whiteout)
    const whiteArmorMat = new THREE.MeshStandardMaterial({
      color: 0xebf1f8,
      roughness: 0.38,
      metalness: 0.04
    });

    // Signature Vibrant Teal Accent (ear fins, chest badge)
    const tealMat = new THREE.MeshStandardMaterial({
      color: 0x00c2cb,
      roughness: 0.25,
      metalness: 0.08
    });

    // Dark seam / joints / earcup sockets
    const darkSeamMat = new THREE.MeshStandardMaterial({
      color: 0x0c1e3d,
      roughness: 0.50,
      metalness: 0.40
    });

    // Deep glossy electric royal blue visor screen (Exact match to reference image!)
    const visorMat = new THREE.MeshPhysicalMaterial({
      color: 0x163cb8,
      roughness: 0.12,
      metalness: 0.10,
      clearcoat: 1.0,
      clearcoatRoughness: 0.08
    });

    // Glowing Cyan Eyes Material (Emissive bright neon on top of electric blue visor)
    this.cyanGlowMat = new THREE.MeshStandardMaterial({
      color: 0x38e1ff,
      emissive: 0x00f0ff,
      emissiveIntensity: 1.6,
      roughness: 0.20
    });

    // 2. CHIBI HEAD ASSEMBLY
    this.head = new THREE.Group();
    this.head.position.set(0, 1.95, 0);

    // Large rounded chibi white helmet shell
    const helmetGeo = new THREE.SphereGeometry(0.54, 32, 28);
    helmetGeo.scale(1.10, 1.02, 1.04);
    const helmet = new THREE.Mesh(helmetGeo, whiteArmorMat);
    this.head.add(helmet);

    // Top Crown Antenna Crest (the cute rounded rectangular bump on top from image)
    const crestGeo = new THREE.BoxGeometry(0.16, 0.085, 0.26);
    const crest = new THREE.Mesh(crestGeo, whiteArmorMat);
    crest.position.set(0, 0.54, -0.02);
    this.head.add(crest);

    // Curved Glossy Visor Face Screen (Inset into front of helmet)
    const visorGeo = new THREE.SphereGeometry(0.48, 32, 24);
    visorGeo.scale(0.96, 0.78, 0.60);
    this.visorMesh = new THREE.Mesh(visorGeo, visorMat);
    this.visorMesh.position.set(0, 0.02, 0.22);
    this.head.add(this.visorMesh);

    // 3. GLOWING CYAN SMILING CURVED EYES (^ ^) - EXACT MATCH TO REFERENCE IMAGE!
    // True 3D luminous arches with rounded end-caps sitting cleanly on the visor front
    const createSmilingEye = (isRight) => {
      const eyeSubGroup = new THREE.Group();
      const sign = isRight ? 1 : -1;

      // Inverted-U Arch: Torus arc of Math.PI rotated so arch curves upward like a smile ^
      const archGeo = new THREE.TorusGeometry(0.065, 0.018, 16, 32, Math.PI);
      const archMesh = new THREE.Mesh(archGeo, this.cyanGlowMat);
      archMesh.rotation.z = Math.PI; // Inverted arch = smile curve ^
      eyeSubGroup.add(archMesh);

      // Smooth rounded end-caps on the arch tips
      const capGeo = new THREE.SphereGeometry(0.018, 16, 16);
      const capL = new THREE.Mesh(capGeo, this.cyanGlowMat);
      capL.position.set(-0.065, 0, 0);
      eyeSubGroup.add(capL);

      const capR = new THREE.Mesh(capGeo, this.cyanGlowMat);
      capR.position.set(0.065, 0, 0);
      eyeSubGroup.add(capR);

      // Position cleanly on front surface of the visor screen
      eyeSubGroup.position.set(sign * 0.15, 0.04, 0.44);
      return eyeSubGroup;
    };

    this.leftEye = createSmilingEye(false);
    this.rightEye = createSmilingEye(true);
    this.eyeGroup.add(this.leftEye);
    this.eyeGroup.add(this.rightEye);
    this.head.add(this.eyeGroup);

    // Scanning horizontal laser bar across visor (active during SEARCHING)
    const scanBarGeo = new THREE.BoxGeometry(0.46, 0.016, 0.02);
    this.scanLaserBar = new THREE.Mesh(scanBarGeo, this.cyanGlowMat);
    this.scanLaserBar.position.set(0, 0.04, 0.45);
    this.scanLaserBar.visible = false;
    this.head.add(this.scanLaserBar);

    // HEADPHONE EARCUPS & TEAL ANTENNA FINS (Iconic Feature from Image!)
    const buildEarUnit = (isRight) => {
      const earGroup = new THREE.Group();
      const sign = isRight ? 1 : -1;
      earGroup.position.set(sign * 0.56, 0.04, 0);

      // White circular outer earcup
      const cupGeo = new THREE.CylinderGeometry(0.15, 0.15, 0.13, 32);
      const cup = new THREE.Mesh(cupGeo, whiteArmorMat);
      cup.rotation.z = Math.PI / 2;
      earGroup.add(cup);

      // Dark circular inset socket
      const socketGeo = new THREE.CylinderGeometry(0.09, 0.09, 0.14, 24);
      const socket = new THREE.Mesh(socketGeo, darkSeamMat);
      socket.rotation.z = Math.PI / 2;
      earGroup.add(socket);

      // Inner glowing cyan audio ring
      const innerRingGeo = new THREE.RingGeometry(0.035, 0.065, 24);
      const innerRingMat = new THREE.MeshBasicMaterial({
        color: 0x00f0ff,
        side: THREE.DoubleSide
      });
      const innerRing = new THREE.Mesh(innerRingGeo, innerRingMat);
      innerRing.rotation.y = sign * (Math.PI / 2);
      innerRing.position.x = sign * 0.072;
      earGroup.add(innerRing);

      // SIGNATURE TEAL ANTENNA FIN (Tapered aerodynamic fin on top of earcup)
      const finGeo = new THREE.BoxGeometry(0.065, 0.26, 0.10);
      const pos = finGeo.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        if (pos.getY(i) > 0.04) {
          pos.setX(i, pos.getX(i) * 0.40);
          pos.setZ(i, pos.getZ(i) * 0.45);
        }
      }
      finGeo.computeVertexNormals();

      const fin = new THREE.Mesh(finGeo, tealMat);
      fin.position.set(0, 0.15, -0.03);
      fin.rotation.z = sign * -0.12; // angle slightly outwards
      fin.rotation.x = -0.20; // angle slightly back
      earGroup.add(fin);

      return earGroup;
    };

    this.head.add(buildEarUnit(false)); // Left ear
    this.head.add(buildEarUnit(true));  // Right ear

    this.robotGroup.add(this.head);

    // 4. FLOATING CHUBBY TORSO (NO LEGS! Exactly as in reference image)
    const torsoGroup = new THREE.Group();
    torsoGroup.position.set(0, 1.25, 0);

    // Smooth rounded white floating capsule / egg body
    const upperTorsoGeo = new THREE.SphereGeometry(0.46, 32, 24);
    upperTorsoGeo.scale(0.95, 1.02, 0.90);
    const upperTorso = new THREE.Mesh(upperTorsoGeo, whiteArmorMat);
    upperTorso.position.y = 0.10;
    torsoGroup.add(upperTorso);

    const lowerTorsoGeo = new THREE.SphereGeometry(0.42, 32, 24);
    lowerTorsoGeo.scale(0.86, 1.10, 0.82);
    const lowerTorso = new THREE.Mesh(lowerTorsoGeo, whiteArmorMat);
    lowerTorso.position.y = -0.22;
    torsoGroup.add(lowerTorso);

    // Dark seam groove separating upper and lower chassis
    const seamGeo = new THREE.TorusGeometry(0.40, 0.012, 16, 48);
    const seam = new THREE.Mesh(seamGeo, darkSeamMat);
    seam.rotation.x = Math.PI / 2;
    seam.position.y = -0.08;
    torsoGroup.add(seam);

    // TEAL CHEST SHIELD BADGE (Tapered rounded shield from reference image)
    const badgeGeo = new THREE.BoxGeometry(0.24, 0.20, 0.03);
    const bpos = badgeGeo.attributes.position;
    for (let i = 0; i < bpos.count; i++) {
      if (bpos.getY(i) < 0) {
        bpos.setX(i, bpos.getX(i) * 0.35); // tapers down to shield point
      }
    }
    badgeGeo.computeVertexNormals();

    this.chestBadge = new THREE.Mesh(badgeGeo, tealMat);
    this.chestBadge.position.set(0, 0.04, 0.38);
    this.chestBadge.rotation.x = -0.12; // curve naturally against torso
    torsoGroup.add(this.chestBadge);

    // HOVER THRUSTER RING UNDERNEATH BASE
    const thrusterGeo = new THREE.RingGeometry(0.10, 0.24, 32);
    const thrusterMat = new THREE.MeshBasicMaterial({
      color: 0x00f0ff,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85
    });
    const thruster = new THREE.Mesh(thrusterGeo, thrusterMat);
    thruster.rotation.x = Math.PI / 2;
    thruster.position.y = -0.58;
    torsoGroup.add(thruster);

    this.robotGroup.add(torsoGroup);

    // 5. ARTICULATED ARMS (Expressive Waving & Hologram Gesturing)
    // Left Arm (Relaxed floating / swaying)
    this.leftArm = new THREE.Group();
    this.leftArm.position.set(-0.48, 1.35, 0.02);

    const shoulderL = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 16), whiteArmorMat);
    this.leftArm.add(shoulderL);

    const armLGeo = new THREE.CylinderGeometry(0.075, 0.065, 0.32, 20);
    const armL = new THREE.Mesh(armLGeo, whiteArmorMat);
    armL.position.set(0, -0.18, 0);
    this.leftArm.add(armL);

    const handLGeo = new THREE.SphereGeometry(0.09, 20, 16);
    handLGeo.scale(1.0, 1.15, 0.85); // cute rounded mitten hand
    const handL = new THREE.Mesh(handLGeo, whiteArmorMat);
    handL.position.set(0, -0.38, 0);
    this.leftArm.add(handL);

    this.leftArm.rotation.z = 0.22; // angled down-outward
    this.robotGroup.add(this.leftArm);

    // Right Arm (THE WAVING ARM matching reference image!)
    this.rightArm = new THREE.Group();
    this.rightArm.position.set(0.48, 1.35, 0.02);

    const shoulderR = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 16), whiteArmorMat);
    this.rightArm.add(shoulderR);

    // Forearm & Hand group for waving
    this.rightForearm = new THREE.Group();
    this.rightForearm.position.set(0, 0, 0);

    const armRGeo = new THREE.CylinderGeometry(0.075, 0.065, 0.32, 20);
    const armR = new THREE.Mesh(armRGeo, whiteArmorMat);
    armR.position.set(0, 0.16, 0);
    this.rightForearm.add(armR);

    const handRGeo = new THREE.SphereGeometry(0.09, 20, 16);
    handRGeo.scale(1.0, 1.15, 0.85);
    const handR = new THREE.Mesh(handRGeo, whiteArmorMat);
    handR.position.set(0, 0.35, 0);
    this.rightForearm.add(handR);

    this.rightArm.add(this.rightForearm);

    // Standby Waving Pose: Right arm raised high waving hello (👋)
    this.rightArm.rotation.z = -0.55;
    this.rightArm.rotation.x = -0.25;
    this.robotGroup.add(this.rightArm);
  }

  buildFloatingHolograms() {
    // Translucent glowing wireframe screens from the video
    const screenMat = new THREE.MeshBasicMaterial({
      color: 0x00f0ff,
      wireframe: true,
      transparent: true,
      opacity: 0.35,
      side: THREE.DoubleSide
    });

    // Left Hologram Screen (Telemetry / Status Terminal)
    const leftGeo = new THREE.PlaneGeometry(1.2, 0.85, 8, 6);
    this.holoScreenLeft = new THREE.Mesh(leftGeo, screenMat);
    this.holoScreenLeft.position.set(-1.6, 1.75, 0.3);
    this.holoScreenLeft.rotation.y = 0.55;
    this.hologramGroup.add(this.holoScreenLeft);

    // Right Hologram Screen (Knowledge Matrix & Verified Records)
    const rightGeo = new THREE.PlaneGeometry(1.0, 0.75, 6, 5);
    this.holoScreenRight = new THREE.Mesh(rightGeo, screenMat);
    this.holoScreenRight.position.set(1.5, 1.85, 0.2);
    this.holoScreenRight.rotation.y = -0.50;
    this.hologramGroup.add(this.holoScreenRight);

    // Orbiting Base Data Hexagons
    for (let i = 0; i < 5; i++) {
      const angle = (i / 5) * Math.PI * 2;
      const radius = 1.6;
      const hex = new THREE.Mesh(
        new THREE.RingGeometry(0.07, 0.09, 6),
        new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide, transparent: true, opacity: 0.65 })
      );
      hex.position.set(Math.cos(angle) * radius, 1.55 + Math.sin(i) * 0.2, Math.sin(angle) * radius);
      this.hologramGroup.add(hex);
      this.orbitingNodes.push({
        mesh: hex,
        angle: angle,
        radius: radius,
        speed: 0.35 + (i % 2) * 0.15,
        baseY: hex.position.y
      });
    }
  }

  buildEnvironment() {
    // Dark floor grid fading into space
    const grid = new THREE.GridHelper(32, 64, 0x00f0ff, 0x0a1428);
    grid.position.y = -0.01;
    this.scene.add(grid);

    // Floating ambient cyan cyber dust
    const count = 250;
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count * 3; i += 3) {
      pos[i] = (Math.random() - 0.5) * 11;
      pos[i + 1] = Math.random() * 4.5;
      pos[i + 2] = (Math.random() - 0.5) * 11;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.026,
      color: 0x00f0ff,
      transparent: true,
      opacity: 0.55,
      blending: THREE.AdditiveBlending
    });
    this.dust = new THREE.Points(geo, mat);
    this.scene.add(this.dust);
  }

  setupPostprocessing() {
    if (typeof THREE.EffectComposer === 'undefined' || typeof THREE.RenderPass === 'undefined' || typeof THREE.UnrealBloomPass === 'undefined') {
      console.warn("[CyberneticScene] Postprocessing shaders not found; using direct WebGL rendering.");
      this.composer = null;
      return;
    }
    try {
      const renderPass = new THREE.RenderPass(this.scene, this.camera);
      const bloomPass = new THREE.UnrealBloomPass(
        new THREE.Vector2(window.innerWidth, window.innerHeight),
        0.35, // gentle bloom strength (NO blinding overblown whiteout)
        0.24, // bloom radius
        0.86  // HIGH bloom threshold: only emissive cyan eyes & neon rings glow, NOT the white body!
      );
      bloomPass.renderToScreen = true;

      this.composer = new THREE.EffectComposer(this.renderer);
      this.composer.addPass(renderPass);
      this.composer.addPass(bloomPass);
    } catch (e) {
      console.warn("[CyberneticScene] Postprocessing fallback to standard WebGL:", e);
      this.composer = null;
    }
  }

  onMouseMove(e) {
    const mouseX = (e.clientX / window.innerWidth) * 2 - 1;
    const mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
    this.targetHeadRotation.y = mouseX * 0.45;
    this.targetHeadRotation.x = -mouseY * 0.22;
  }

  onWindowResize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    if (this.composer) this.composer.setSize(w, h);
  }

  // REAL-TIME BACKEND SYNCHRONIZATION METHODS
  setAgentState(state) {
    this.agentState = state || "STANDBY";

    // Eye color transitions
    const colorMap = {
      STANDBY: 0x00f0ff,
      PLANNING: 0x00f0ff,
      SEARCHING: 0x00f0ff,
      COLLECTING: 0x00ffa3,
      EXTRACTING: 0x00ffa3,
      VERIFYING: 0xffb800,
      COMPLETED: 0x00ffa3
    };
    const c = colorMap[state] || 0x00f0ff;
    if (this.cyanGlowMat) {
      this.cyanGlowMat.color.setHex(c);
    }

    if (this.scanLaserBar) {
      this.scanLaserBar.visible = (state === "SEARCHING");
    }

    if (state === "SEARCHING") {
      this.radarRipples.forEach((r) => {
        r.active = true;
        r.mesh.material.opacity = 0.8;
      });
    }

    if (state === "COMPLETED") {
      this.spawnCelebrationBurst();
    }
  }

  onSearchResult(query, count) {
    this.setAgentState("SEARCHING");
    if (this.holoScreenLeft) {
      this.holoScreenLeft.material.color.setHex(0x00ffa3);
      setTimeout(() => {
        if (this.holoScreenLeft) this.holoScreenLeft.material.color.setHex(0x00f0ff);
      }, 600);
    }
  }

  onPageFetched(url, status) {
    const isSuccess = status >= 200 && status < 300;
    const color = isSuccess ? 0x00ffa3 : 0xffb800;
    this.spawnIncomingDataPacket(color);
  }

  onRecordExtracted(entityName) {
    const nodeGeo = new THREE.OctahedronGeometry(0.12, 1);
    const nodeMat = new THREE.MeshStandardMaterial({
      color: 0x00f0ff,
      roughness: 0.25,
      metalness: 0.6,
      wireframe: false
    });
    const mesh = new THREE.Mesh(nodeGeo, nodeMat);

    const halo = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.15, 0),
      new THREE.MeshBasicMaterial({ color: 0x00f0ff, wireframe: true })
    );
    mesh.add(halo);

    const angle = Math.random() * Math.PI * 2;
    const radius = 1.35 + Math.random() * 0.35;
    mesh.position.set(Math.cos(angle) * radius, 1.4 + Math.random() * 0.4, Math.sin(angle) * radius);
    this.orbitingNodesGroup.add(mesh);

    this.orbitingNodes.push({
      mesh: mesh,
      angle: angle,
      radius: radius,
      speed: 0.4 + Math.random() * 0.2,
      baseY: mesh.position.y,
      isVerified: false
    });

    if (this.orbitingNodes.length > 10) {
      const oldest = this.orbitingNodes.shift();
      if (oldest && oldest.mesh) {
        this.orbitingNodesGroup.remove(oldest.mesh);
      }
    }
  }

  onRecordVerified(entityName, status, confidence) {
    const candidate = this.orbitingNodes.find(n => !n.isVerified);
    if (candidate && candidate.mesh) {
      candidate.isVerified = true;
      candidate.mesh.material.color.setHex(0x00ffa3);
      candidate.mesh.scale.set(1.3, 1.3, 1.3);
      this.spawnVerificationBeam(candidate.mesh.position);
    }
  }

  spawnVerificationBeam(targetPos) {
    const points = [
      new THREE.Vector3(0.48, 1.45, 0.2), // right hand
      targetPos.clone()
    ];
    const geo = new THREE.BufferGeometry().setFromPoints(points);
    const mat = new THREE.LineBasicMaterial({
      color: 0x00ffa3,
      transparent: true,
      opacity: 0.95
    });
    const line = new THREE.Line(geo, mat);
    this.effectsGroup.add(line);

    setTimeout(() => {
      this.effectsGroup.remove(line);
      geo.dispose();
      mat.dispose();
    }, 450);
  }

  spawnIncomingDataPacket(colorHex) {
    const packetGeo = new THREE.BoxGeometry(0.07, 0.07, 0.07);
    const packetMat = new THREE.MeshBasicMaterial({ color: colorHex, wireframe: true });
    const packet = new THREE.Mesh(packetGeo, packetMat);

    const angle = Math.random() * Math.PI * 2;
    packet.position.set(Math.cos(angle) * 3.2, 1.4 + Math.random() * 0.6, Math.sin(angle) * 3.2);
    this.effectsGroup.add(packet);

    this.incomingPackets.push({
      mesh: packet,
      progress: 0.0,
      startPos: packet.position.clone(),
      targetPos: new THREE.Vector3(0, 1.3, 0.3)
    });
  }

  spawnCelebrationBurst() {
    if (this.pedestalRing) {
      this.pedestalRing.material.color.setHex(0x00ffa3);
    }
    if (this.hoverLight) {
      this.hoverLight.color.setHex(0x00ffa3);
    }
  }

  // MAIN RENDER & ANIMATION LOOP
  animate() {
    requestAnimationFrame(() => this.animate());
    if (typeof document !== "undefined" && document.hidden) return;

    const delta = this.clock.getDelta();
    const time = this.clock.getElapsedTime();

    // 1. Floating Hover Dynamics: Smooth gentle floating bobbing (No Legs!)
    const hoverY = 0.06 * Math.sin(time * 2.2) + 0.02 * Math.sin(time * 0.8);
    this.robotGroup.position.y = hoverY;

    // Subtle responsive tilt/banking towards cursor
    this.robotGroup.rotation.z = -this.targetHeadRotation.y * 0.10;
    this.robotGroup.rotation.x = this.targetHeadRotation.x * 0.08;

    // 2. Head Tracking: Smooth looking towards mouse
    if (this.head) {
      this.head.rotation.y += (this.targetHeadRotation.y - this.head.rotation.y) * 0.08;
      this.head.rotation.x += (this.targetHeadRotation.x - this.head.rotation.x) * 0.08;
    }

    // 3. Eye Blinking & Facial Animations
    this.blinkTimer += delta;
    if (this.blinkTimer > 4.5) {
      this.isBlinking = true;
      if (this.blinkTimer > 4.66) {
        this.isBlinking = false;
        this.blinkTimer = 0;
      }
    }

    if (this.eyeGroup) {
      if (this.isBlinking && this.agentState === "STANDBY") {
        this.eyeGroup.scale.y = 0.12; // Flat line blink - -
      } else {
        this.eyeGroup.scale.y = 1.0;  // Open smiling happy eyes ^ ^
      }
    }

    // Sweeping laser scanner bar in SEARCHING mode
    if (this.scanLaserBar && this.scanLaserBar.visible) {
      this.scanLaserBar.position.y = 0.04 + Math.sin(time * 4.5) * 0.10;
    }

    // 4. Arm Gestures:
    // Left Arm gentle breathing sway
    if (this.leftArm) {
      this.leftArm.rotation.x = Math.sin(time * 1.8) * 0.06;
    }

    // Right Arm:
    // When in STANDBY: Wave hello (👋) matching reference image!
    // When in ACTIVE (Searching/Extracting): Point forward or conduct holograms!
    if (this.rightArm && this.rightForearm) {
      if (this.agentState === "STANDBY") {
        // Raised waving greeting
        const targetRotZ = -0.55 + Math.sin(time * 5.0) * 0.20;
        const targetRotX = -0.30 + Math.cos(time * 2.5) * 0.08;
        this.rightArm.rotation.z += (targetRotZ - this.rightArm.rotation.z) * 0.10;
        this.rightArm.rotation.x += (targetRotX - this.rightArm.rotation.x) * 0.10;
        this.rightForearm.rotation.z = Math.sin(time * 6.0) * 0.20; // waving wrist
      } else if (this.agentState === "COMPLETED") {
        // Joyful celebration wave
        const targetRotZ = -0.70 + Math.sin(time * 7.5) * 0.28;
        this.rightArm.rotation.z += (targetRotZ - this.rightArm.rotation.z) * 0.14;
        this.rightArm.rotation.x += (-0.25 - this.rightArm.rotation.x) * 0.10;
        this.rightForearm.rotation.z = Math.sin(time * 8.0) * 0.32;
      } else {
        // Active researching: point forward towards holographic screens
        const targetRotZ = -0.15;
        const targetRotX = -Math.PI / 2.6 + Math.sin(time * 3.0) * 0.08;
        this.rightArm.rotation.z += (targetRotZ - this.rightArm.rotation.z) * 0.08;
        this.rightArm.rotation.x += (targetRotX - this.rightArm.rotation.x) * 0.08;
        this.rightForearm.rotation.z = Math.sin(time * 3.0) * 0.10;
      }
    }

    // 5. Hover Thruster & Dais Lighting Pulsing
    if (this.hoverLight) {
      const pulse = 1.0 + Math.sin(time * 3.0) * 0.25;
      this.hoverLight.intensity = pulse;
    }

    // 6. Pedestal Neon Rings Rotation
    if (this.pedestalRing) {
      this.pedestalRing.rotation.z += 0.25 * delta;
    }
    if (this.hexRing) {
      this.hexRing.rotation.z -= 0.35 * delta;
    }

    // 7. Radar Ripples on Dais (Active during SEARCHING)
    this.radarRipples.forEach(r => {
      if (r.active) {
        r.radius += r.speed * delta;
        r.mesh.scale.set(r.radius, r.radius, 1);
        r.mesh.material.opacity = Math.max(0, 0.8 - (r.radius / 1.8) * 0.8);
        if (r.radius > 1.8) {
          r.radius = 0.3;
          if (this.agentState !== "SEARCHING") {
            r.active = false;
            r.mesh.material.opacity = 0;
          }
        }
      }
    });

    // 8. Floating Hologram Screens Sway
    if (this.holoScreenLeft) {
      this.holoScreenLeft.position.y = 1.75 + Math.sin(time * 2.0) * 0.04;
    }
    if (this.holoScreenRight) {
      this.holoScreenRight.position.y = 1.85 + Math.cos(time * 1.7) * 0.04;
    }

    // 9. Orbiting 3D Data Nodes
    this.orbitingNodes.forEach(n => {
      n.angle += n.speed * delta;
      n.mesh.position.x = Math.cos(n.angle) * n.radius;
      n.mesh.position.z = Math.sin(n.angle) * n.radius;
      n.mesh.position.y = n.baseY + Math.sin(time * 2.5 + n.angle) * 0.07;
      n.mesh.rotation.y += delta * 1.5;
      n.mesh.rotation.x += delta * 0.8;
    });

    // 10. Incoming Ingestion Data Packets (Flying towards robot chest)
    for (let i = this.incomingPackets.length - 1; i >= 0; i--) {
      const p = this.incomingPackets[i];
      p.progress += delta * 1.8;
      p.mesh.position.lerpVectors(p.startPos, p.targetPos, p.progress);
      p.mesh.rotation.x += delta * 6;
      p.mesh.rotation.y += delta * 6;
      if (p.progress >= 1.0) {
        this.effectsGroup.remove(p.mesh);
        p.mesh.geometry.dispose();
        p.mesh.material.dispose();
        this.incomingPackets.splice(i, 1);
      }
    }

    // 11. Ambient Floating Cyber Dust
    if (this.dust) {
      this.dust.rotation.y += 0.010 * delta;
    }

    if (this.controls) {
      this.controls.update();
    }

    if (this.composer) {
      this.composer.render();
    } else {
      this.renderer.render(this.scene, this.camera);
    }
  }
}

window.CyberneticAgentScene = CyberneticAgentScene;
