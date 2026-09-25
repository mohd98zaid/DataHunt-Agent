// Main App Orchestration: Clean video matching states, WebSocket events, and Report Drawer
function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
function sanitizeUrl(url) {
    if (!url) return '#';
    try {
        const u = new URL(url, window.location.origin);
        return ['http:', 'https:'].includes(u.protocol) ? url : '#';
    } catch {
        return '#';
    }
}

window.trackCockpitApply = async function(recId, btnEl, event) {
  if (event) event.stopPropagation();
  if (btnEl) {
    btnEl.innerHTML = '<span>APPLIED ✓</span>';
    btnEl.style.borderColor = 'var(--neon-green)';
    btnEl.style.color = 'var(--neon-green)';
  }
  if (recId) {
    try {
      await fetch(`/api/jobs/${recId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ applied_status: "applied" })
      });
    } catch (e) {
      console.debug("Cockpit apply tracking:", e);
    }
  }
};

let scene = null;
let hud = null;
let socket = null;
let isRunning = false;
let latestSummaryMarkdown = "";

window.resetToInputMode = function() {
  isRunning = false;
  const promptInputRow = document.getElementById("prompt-input-row");
  const analyzingBanner = document.getElementById("analyzing-banner");
  const viewResultsBtn = document.getElementById("view-results-btn");
  const newQueryBtn = document.getElementById("new-query-btn");
  const cardStateLabel = document.getElementById("card-state-label");
  const queryInput = document.getElementById("query-input");
  const engageBtnText = document.getElementById("engage-btn-text");
  const resultsModal = document.getElementById("results-modal");
  const tickerText = document.getElementById("ticker-text");

  if (resultsModal) resultsModal.style.display = "none";
  const centerResultsPanel = document.getElementById("center-results-panel");
  if (centerResultsPanel) centerResultsPanel.style.display = "none";
  const promptCard = document.getElementById("center-prompt-card");
  const dialoguePill = document.getElementById("bot-dialogue-pill");
  if (promptCard) promptCard.style.display = "flex";
  if (dialoguePill) dialoguePill.style.display = "flex";
  if (promptInputRow) promptInputRow.style.display = "flex";
  if (analyzingBanner) analyzingBanner.style.display = "none";
  const analyzingSpinner = document.getElementById("analyzing-spinner");
  if (analyzingSpinner) {
    analyzingSpinner.classList.remove("completed", "failed");
  }
  if (viewResultsBtn) viewResultsBtn.style.display = "none";
  if (newQueryBtn) newQueryBtn.style.display = "none";
  if (cardStateLabel) cardStateLabel.textContent = "Waiting...";
  if (engageBtnText) engageBtnText.textContent = "Send";
  if (tickerText) tickerText.textContent = "Robot ready. Standing by on circular pedestal.";
  const speechText = document.getElementById("dialogue-speech-text");
  if (speechText) speechText.textContent = "Ready for directives. Standing by on circular pedestal 👋";

  if (scene && typeof scene.setAgentState === "function") {
    scene.setAgentState("STANDBY");
  }
  if (hud && typeof hud.setPhaseState === "function") {
    hud.setPhaseState("IDLE");
  }
  if (hud && typeof hud.setWaveformActivity === "function") {
    hud.setWaveformActivity(0.3);
  }

  if (queryInput) {
    queryInput.value = "";
    queryInput.focus();
  }
};

let currentFreshness = "0sec";
const urlParams = new URLSearchParams(window.location.search);
let currentAgentMode = urlParams.get("mode") || "research";

function configureAgentModeUI(mode) {
  currentAgentMode = mode;
  const agentTag = document.getElementById("cockpit-agent-tag");
  const modeBadge = document.getElementById("active-mode-badge");
  const queryInput = document.getElementById("query-input");
  const presetsTitle = document.getElementById("presets-panel-title");
  const presetsContainer = document.getElementById("dynamic-presets-container");

  if (mode === "jobs") {
    if (agentTag) agentTag.textContent = "🎯 0-SEC JOB RADAR";
    if (modeBadge) {
      modeBadge.textContent = "AGENT: JOB RADAR";
      modeBadge.style.borderColor = "var(--neon-green)";
      modeBadge.style.color = "var(--neon-green)";
    }
    if (queryInput) queryInput.placeholder = "e.g. Find senior AI engineer jobs in Dubai 0-sec";
    if (presetsTitle) presetsTitle.textContent = "JOB RADAR DIRECTIVES";
    if (presetsContainer) {
      presetsContainer.innerHTML = `
        <div class="preset-label">⚡ Live ATS Directives</div>
        <div class="query-presets">
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('AI Engineer');">⚡ 0-Sec AI Roles (ATS)</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('GenAI Dubai');">GenAI Jobs Dubai</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('Senior Machine Learning Engineer');">ML Engineer Roles</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('Remote Staff Python Engineer');">Staff Python Roles</div>
        </div>
      `;
    }
  } else if (mode === "market") {
    if (agentTag) agentTag.textContent = "📊 MARKET INTEL AGENT";
    if (modeBadge) {
      modeBadge.textContent = "AGENT: MARKET INTEL";
      modeBadge.style.borderColor = "var(--neon-purple)";
      modeBadge.style.color = "var(--neon-purple)";
    }
    if (queryInput) queryInput.placeholder = "e.g. Competitive pricing & feature analysis of vector databases";
    if (presetsTitle) presetsTitle.textContent = "MARKET DIRECTIVES";
    if (presetsContainer) {
      presetsContainer.innerHTML = `
        <div class="preset-label">📊 Competitive Landscapes</div>
        <div class="query-presets">
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('compare vector databases milvus qdrant chroma pgvector pricing features');">Vector DB Feature Matrix</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('top AI agent development platforms for enterprise market analysis');">AI Agent Platforms</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('AI developer tools market share and growth rate 2026');">AI DevTools Landscape</div>
        </div>
      `;
    }
  } else {
    // Default: research
    if (agentTag) agentTag.textContent = "🔬 DEEP RESEARCH AGENT";
    if (modeBadge) {
      modeBadge.textContent = "AGENT: DEEP RESEARCH";
      modeBadge.style.borderColor = "var(--neon-cyan)";
      modeBadge.style.color = "var(--neon-cyan)";
    }
    if (queryInput) queryInput.placeholder = "Deploy research directive e.g. What is LangChain and how does LCEL work?";
    if (presetsTitle) presetsTitle.textContent = "RESEARCH DIRECTIVES";
    if (presetsContainer) {
      presetsContainer.innerHTML = `
        <div class="preset-label">🔬 Knowledge Synthesis</div>
        <div class="query-presets">
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('What is LangChain and how does LCEL work?');">LangChain Architecture</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('Compare Rust vs Go for high-concurrency microservices');">Rust vs Go Microservices</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('top 3 open source AI agent frameworks and github stars');">AI Agent Frameworks</div>
          <div class="preset-chip" onclick="if (window.setQuery) window.setQuery('DeepSeek R1 reasoning architecture and reinforcement learning details');">DeepSeek R1 Architecture</div>
        </div>
      `;
    }
  }
}

window.setFreshness = function(mode) {
  currentFreshness = mode;
  document.querySelectorAll(".freshness-toggle-group:not(#model-toggle-group) .freshness-chip").forEach(el => el.classList.remove("active"));
  const activeBtn = document.getElementById(`freshness-chip-${mode}`);
  if (activeBtn) activeBtn.classList.add("active");
  console.log("[DataHunt] Freshness radar mode set to:", mode);
  if (hud && typeof hud.appendLog === "function") {
    hud.appendLog("RADAR", `Freshness filter set to: ${mode.toUpperCase()}`);
  }
  if (window.SoundFX && typeof window.SoundFX.playClick === "function") {
    window.SoundFX.playClick();
  }

  // Live filter visible job radar cards if results are already displayed
  if (latestRunRecords && latestRunRecords.length > 0) {
    let filtered = latestRunRecords;
    if (mode === "0sec") {
      filtered = latestRunRecords.filter(r => {
        const f = r.fields || {};
        const badge = String(f.freshness_badge || "").toUpperCase();
        const age = f.posted_age_seconds;
        return (age !== undefined && age < 3600) ||
               badge.includes("0-SEC") || badge.includes("JUST NOW") ||
               badge.includes("S AGO") || badge.includes("M AGO") || badge.includes("TODAY");
      });
      if (filtered.length === 0) {
        filtered = latestRunRecords.filter(r => {
          const age = r.fields?.posted_age_seconds;
          return age !== undefined && age <= 86400;
        });
        if (filtered.length === 0) filtered = latestRunRecords;
      }
    } else if (mode === "24h") {
      filtered = latestRunRecords.filter(r => {
        const f = r.fields || {};
        const badge = String(f.freshness_badge || "").toUpperCase();
        const age = f.posted_age_seconds;
        return (age !== undefined && age <= 86400) ||
               badge.includes("0-SEC") || badge.includes("JUST NOW") ||
               badge.includes("TODAY") || badge.includes("24H") || badge.includes("HOUR") || badge.includes("HR");
      });
      if (filtered.length === 0) filtered = latestRunRecords;
    }
    if (typeof renderOnPageResults === "function") {
      renderOnPageResults(latestSummaryMarkdown, filtered, lastQueryText, { pages_fetched: 1 });
    }
  }
};

let currentModel = "auto";

window.setModel = function(mode) {
  currentModel = mode;
  document.querySelectorAll("#model-toggle-group .freshness-chip").forEach(el => el.classList.remove("active"));
  let chipId = "model-chip-auto";
  let labelText = "🔄 MULTI-MODEL";
  if (mode.includes("lite")) {
    chipId = "model-chip-lite";
    labelText = "⚡ LITE";
  } else if (mode.includes("flash") || mode.includes("3.8")) {
    chipId = "model-chip-flash";
    labelText = "🚀 3.8 FLASH";
  }
  const activeBtn = document.getElementById(chipId);
  if (activeBtn) activeBtn.classList.add("active");
  const modelTag = document.getElementById("active-model-tag");
  if (modelTag) modelTag.textContent = labelText;
  console.log("[DataHunt] AI model engine set to:", mode);
  if (hud && typeof hud.appendLog === "function") {
    hud.appendLog("MODEL", `Neural Core configured: ${labelText}`);
  }
  if (window.SoundFX && typeof window.SoundFX.playClick === "function") {
    window.SoundFX.playClick();
  }
};

window.setQuery = function(q) {
  console.log("[DataHunt] setQuery selected:", q);
  window.resetToInputMode();
  lastQueryText = q;
  latestRunRecords = [];
  const queryInput = document.getElementById("query-input");
  if (queryInput) {
    queryInput.value = q;
    queryInput.focus();
  }
};

let lastQueryText = "";
let latestRunRecords = [];
let runConfidenceSum = 0;
let runConfidenceCount = 0;

function isSubstantiveRecord(rec) {
  if (!rec || !rec.fields || typeof rec.fields !== "object") return false;
  const f = rec.fields;
  const target = (f.record && typeof f.record === "object") ? f.record : f;
  
  let validFieldCount = 0;
  for (const [k, v] of Object.entries(target)) {
    if (v === null || v === undefined) continue;
    if (["reasons", "checks", "explanation", "error"].includes(k.toLowerCase())) continue;
    const str = String(v).trim().toLowerCase();
    if (!str || str === "null" || str === "none" || str === "{}" || str === "[]") continue;
    if (str.includes("all fields are correctly set to null") || str.includes("no matching") || str.includes("evidence is insufficient")) continue;
    validFieldCount++;
  }
  return validFieldCount > 0;
}

function isJobIntent(query, records) {
  if (currentAgentMode === "jobs") return true;
  if (currentAgentMode === "market" || currentAgentMode === "research") return false;
  const q = (query || lastQueryText || "").toLowerCase();
  const jobKeywords = [
    "job", "jobs", "hiring", "hire", "career", "careers", 
    "intern", "internship", "vacancy", "vacancies", "recruitment", 
    "work as", "apply for", "developer role", "engineer role", "analyst role",
    "engineer", "developer", "designer", "role", "roles"
  ];
  if (jobKeywords.some(k => q.includes(k))) {
    return true;
  }
  if (records && records.length > 0) {
    const substantive = records.filter(isSubstantiveRecord);
    const hasAtsUrl = substantive.some(r => {
      const u = (r.canonical_url || r.fields?.application_url || "").toLowerCase();
      return u.includes("greenhouse.io") || u.includes("lever.co") || u.includes("ashbyhq.com") || u.includes("workable.com") || u.includes("smartrecruiters.com") || u.includes("/careers/") || u.includes("/jobs/");
    });
    const hasJobSpecificFields = substantive.some(r => r.record_type === "job_listing" || (r.fields && (r.fields.salary || r.fields.employment_type || r.fields.company)));
    if (hasAtsUrl || hasJobSpecificFields) {
      return true;
    }
  }
  return false;
}

function isMarketIntent(query, records) {
  if (currentAgentMode === "market") return true;
  if (currentAgentMode === "jobs") return false;
  const q = (query || lastQueryText || "").toLowerCase();
  const marketKeywords = ["pricing", "competitor", "market", "saas", "vs ", "alternative", "alternatives"];
  if (marketKeywords.some(k => q.includes(k))) return true;
  if (records && records.length > 0) {
    return records.some(r => r.record_type === "market_intel" || (r.fields && (r.fields.pricing_model || r.fields.company_name)));
  }
  return false;
}

function renderJobRadarUI(summaryMarkdown, records) {
  const validRecords = (records || []).filter(isSubstantiveRecord);
  let html = `
    <div class="job-radar-results-header">
      <div class="job-radar-results-title">
        <span>⚡ DIRECT ATS VERIFIED OPENINGS</span>
        <span class="job-radar-results-count">${validRecords.length} OPPORTUNITIES LOCATED</span>
      </div>
      <div style="font-family: 'Share Tech Mono', monospace; font-size: 12px; color: var(--text-muted); margin-top: 4px;">
        Zero recruiter spam • High-precision radar filters applied • Direct Application Links
      </div>
    </div>
    <div class="job-card-grid">
  `;

  if (validRecords.length === 0) {
    html += `
      <div style="grid-column: 1 / -1; padding: 24px; text-align: center; color: var(--text-muted); font-family: 'Share Tech Mono', monospace;">
        NO DIRECT ATS OPENINGS DETECTED FOR CURRENT QUERY.
      </div>
    `;
  }

  validRecords.forEach(rec => {
    const f = rec.fields || {};
    const title = f.title || f.name || "Open Role";
    const company = f.company || "Direct Employer";
    const location = f.location || "Remote / Unspecified";
    const salary = f.salary || null;
    const badge = f.freshness_badge || (f.posted_age_seconds !== undefined && f.posted_age_seconds < 60 ? "⚡ 0-SEC / JUST NOW" : (f.posted_at || "Recent"));
    const rawApplyUrl = f.application_url || rec.canonical_url || "#";
    const applyUrl = sanitizeUrl(rawApplyUrl);
    const isFresh = badge.includes("0-SEC") || badge.includes("JUST NOW") || badge.includes("s ago") || badge.includes("m ago") || badge.includes("Today");
    const confScore = Math.round((rec.confidence !== undefined && rec.confidence !== null ? rec.confidence : 0.95) * 100);

    html += `
      <div class="job-card-item">
        <div class="job-card-header">
          <div>
            <span class="job-card-title">${escapeHtml(title)}</span>
            <span style="margin: 0 6px; color: var(--neon-cyan);">@</span>
            <span class="job-card-company">${escapeHtml(company)}</span>
          </div>
          <span class="${isFresh ? 'zero-sec-badge' : 'phase-badge'}">${escapeHtml(badge)}</span>
        </div>
        <div class="job-card-meta-row">
          <span>📍 ${escapeHtml(location)}</span>
          ${salary ? `<span style="color: var(--neon-green); font-weight: 700;">💰 ${escapeHtml(salary)}</span>` : ''}
          <span>🕒 Timestamp: ${escapeHtml(f.posted_at || 'Just now')}</span>
          <span>🛡️ Confidence: ${confScore}%</span>
        </div>
        <div style="display: flex; justify-content: flex-end; margin-top: 6px;">
          <a href="${sanitizeUrl(applyUrl)}" target="_blank" rel="noopener noreferrer" class="direct-apply-btn" onclick="trackCockpitApply('${rec.id}', this, event)">
            <span>APPLY DIRECTLY ON ATS</span>
            <span>➔</span>
          </a>
        </div>
      </div>
    `;
  });

  html += `</div>`;
  html += `
    <div style="margin-top: 24px; border-top: 1px solid rgba(0,240,255,0.2); padding-top: 16px;">
      <h3 style="font-family: 'Orbitron', sans-serif; color: var(--neon-cyan); margin: 0 0 10px 0; font-size: 14px;">AGENT SUMMARY & COVERAGE</h3>
      ${typeof marked !== "undefined" ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(summaryMarkdown)) : escapeHtml(summaryMarkdown)) : escapeHtml(summaryMarkdown)}
    </div>
  `;
  return html;
}

function renderResearchDossierUI(summaryMarkdown, records, query) {
  const validRecords = (records || []).filter(isSubstantiveRecord);
  let html = `
    <div class="research-dossier-header">
      <div class="research-dossier-title-row">
        <div class="research-dossier-title">
          <span>🔬 AUTONOMOUS RESEARCH DOSSIER</span>
        </div>
        <div class="research-stat-badge">
          ${validRecords.length > 0 ? `${validRecords.length} VERIFIED EVIDENCE ANCHORS` : 'SYNTHESIS COMPLETE'}
        </div>
      </div>
      <div style="font-family: 'Share Tech Mono', monospace; font-size: 12px; color: var(--text-muted);">
        Research Directive: "${query || lastQueryText || 'General Research Analysis'}" • Verified against primary public web sources
      </div>
    </div>
  `;

  // If structured entities were extracted, render the Knowledge & Entity Matrix!
  if (validRecords.length > 0) {
    html += `
      <div style="margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between;">
        <span style="font-family: 'Orbitron', sans-serif; font-size: 13px; color: var(--neon-cyan); letter-spacing: 0.8px;">
          ENTITY & KNOWLEDGE MATRIX
        </span>
        <span style="font-family: 'Share Tech Mono', monospace; font-size: 10px; color: var(--text-muted);">
          STRUCTURED FINDINGS
        </span>
      </div>
      <div class="research-entity-grid">
    `;

    validRecords.forEach((rec, idx) => {
      const f = rec.fields || {};
      const entityName = f.name || f.title || f.framework || f.model || f.tool || f.company || `Discovered Finding #${idx + 1}`;
      const sourceUrl = rec.canonical_url || f.url || "#";
      let domain = "web source";
      try { domain = new URL(sourceUrl).hostname; } catch(e) {}

      // Extract primary evidence quote
      let evidenceQuote = "";
      const ev = rec.evidence || f.evidence || f._evidence;
      if (Array.isArray(ev) && ev.length > 0) {
        evidenceQuote = ev[0].evidence_text || ev[0].quote || ev[0].selector || "";
      } else if (ev && typeof ev === "object") {
        for (const val of Object.values(ev)) {
          if (typeof val === "object" && (val.quote || val.selector || val.evidence_text)) {
            evidenceQuote = val.quote || val.selector || val.evidence_text;
            break;
          } else if (typeof val === "string") {
            evidenceQuote = val;
            break;
          }
        }
      }
      if (!evidenceQuote && f.description) {
        evidenceQuote = f.description;
      }

      // Collect attribute tags (e.g. stars, license, language, version, category, features)
      const tags = [];
      for (const [key, val] of Object.entries(f)) {
        if (!val) continue;
        const k = key.toLowerCase();
        if (["name", "title", "framework", "description", "posted_at", "posted_age_seconds", "freshness_badge", "application_url"].includes(k)) continue;
        
        let label = key.replace(/_/g, " ").toUpperCase();
        let displayVal = typeof val === "object" ? JSON.stringify(val) : String(val);
        if (displayVal.length > 40) displayVal = displayVal.substring(0, 37) + "...";
        
        const isHighlight = k.includes("star") || k.includes("metric") || k.includes("score") || k.includes("rank");
        tags.push({ label, val: displayVal, highlight: isHighlight });
      }

      const safeSourceUrl = sanitizeUrl(sourceUrl);
      const confScore = Math.round((rec.confidence !== undefined && rec.confidence !== null ? rec.confidence : 0.95) * 100);

      html += `
        <div class="research-entity-card">
          <div class="research-entity-top">
            <div class="research-entity-name">${escapeHtml(entityName)}</div>
            <div style="font-family: 'Share Tech Mono', monospace; font-size: 11px; color: var(--neon-green); background: rgba(0,255,163,0.1); padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(0,255,163,0.3);">
              CONFIDENCE: ${confScore}%
            </div>
          </div>

          ${tags.length > 0 ? `
            <div class="research-tag-row">
              ${tags.map(t => `<span class="research-tag-pill ${t.highlight ? 'highlight' : ''}"><strong>${escapeHtml(t.label)}:</strong> ${escapeHtml(t.val)}</span>`).join("")}
            </div>
          ` : ''}

          ${evidenceQuote ? `
            <div class="research-quote-box">
              "${escapeHtml(evidenceQuote)}"
            </div>
          ` : ''}

          <div class="research-card-actions">
            <span style="font-family: 'Share Tech Mono', monospace; font-size: 11px; color: var(--text-muted);">
              Primary Source: <strong>${escapeHtml(domain)}</strong>
            </span>
            <a href="${sanitizeUrl(safeSourceUrl)}" target="_blank" rel="noopener noreferrer" class="source-citation-btn">
              <span>EXPLORE SOURCE CITATION</span>
              <span>↗</span>
            </a>
          </div>
        </div>
      `;
    });

    html += `</div>`;
  }

  // Executive Synthesis & Technical Report
  html += `
    <div class="research-executive-card">
      <div class="research-executive-title">
        <span>📊 EXECUTIVE INTELLIGENCE SUMMARY</span>
      </div>
      <div class="markdown-body" style="color: #d8e6f7; line-height: 1.6;">
        ${typeof marked !== "undefined" ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(summaryMarkdown)) : escapeHtml(summaryMarkdown)) : summaryMarkdown}
      </div>
    </div>
  `;

  return html;
}

function renderMarketIntelUI(summaryMarkdown, records, query) {
  const validRecords = (records || []).filter(isSubstantiveRecord);
  let html = `
    <div class="research-dossier-header">
      <div class="research-dossier-title-row">
        <div class="research-dossier-title">
          <span>📊 COMPETITIVE INTELLIGENCE & SAAS PRICING MATRIX</span>
        </div>
        <div class="research-stat-badge">
          ${validRecords.length > 0 ? `${validRecords.length} VENDORS BENCHMARKED` : 'MARKET SYNTHESIS'}
        </div>
      </div>
      <div style="font-family: 'Share Tech Mono', monospace; font-size: 12px; color: var(--text-muted);">
        Market Analysis: "${escapeHtml(query || lastQueryText || 'SaaS Competitors')}" • Verified against primary pricing pages and product specifications
      </div>
    </div>
  `;

  if (validRecords.length > 0) {
    html += `
      <div style="margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between;">
        <span style="font-family: 'Orbitron', sans-serif; font-size: 13px; color: var(--neon-cyan); letter-spacing: 0.8px;">
          COMPETITOR & PRICING MATRIX
        </span>
        <span style="font-family: 'Share Tech Mono', monospace; font-size: 10px; color: var(--text-muted);">
          BENCHMARKED VENDORS
        </span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; margin-bottom: 24px;">
    `;

    validRecords.forEach((rec, idx) => {
      const f = rec.fields || {};
      const compName = f.company_name || f.product_name || f.name || `Solution #${idx + 1}`;
      const pricingModel = f.pricing_model || "Tiered / Usage";
      const startingPrice = f.starting_price || "Free Tier Available";
      const target = f.target_audience || "Engineering Teams";
      const features = Array.isArray(f.key_features) ? f.key_features : (f.key_features ? [f.key_features] : []);
      const strengths = f.strengths || "";
      const url = f.website_url || rec.canonical_url || "#";
      const confScore = Math.round((rec.confidence !== undefined && rec.confidence !== null ? rec.confidence : 0.95) * 100);

      html += `
        <div class="job-card-item" style="border-left: 3px solid var(--neon-cyan);">
          <div class="job-card-header" style="margin-bottom: 8px;">
            <span class="job-card-title" style="font-size: 15px; color: #fff;">${escapeHtml(compName)}</span>
            <span class="phase-badge" style="background: rgba(0, 240, 255, 0.15); border-color: var(--neon-cyan); color: var(--neon-cyan); font-size: 11px;">
              🏷️ ${escapeHtml(pricingModel)}
            </span>
          </div>
          <div class="job-card-meta-row" style="margin-bottom: 8px; gap: 8px;">
            <span style="color: var(--neon-green); font-weight: bold;">💰 ${escapeHtml(startingPrice)}</span>
            <span>🎯 ${escapeHtml(target)}</span>
            <span>🛡️ ${confScore}%</span>
          </div>
          ${features.length > 0 ? `
            <div style="font-size: 12px; color: #b8cde6; margin-bottom: 8px; line-height: 1.4;">
              <strong style="color: var(--neon-cyan); font-family: 'Share Tech Mono', monospace; font-size: 10px;">CORE CAPABILITIES:</strong>
              <ul style="margin: 4px 0 0 16px; padding: 0;">
                ${features.slice(0, 3).map(feat => `<li>${escapeHtml(feat)}</li>`).join("")}
              </ul>
            </div>
          ` : ''}
          ${strengths ? `
            <div class="research-quote-box" style="margin: 6px 0; font-size: 11px; padding: 6px 8px;">
              ⚡ <em>${escapeHtml(strengths)}</em>
            </div>
          ` : ''}
          <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
            <a href="${sanitizeUrl(url)}" target="_blank" rel="noopener noreferrer" class="direct-apply-btn" style="border-color: var(--neon-cyan); color: var(--neon-cyan);">
              <span>VISIT SOLUTION ➔</span>
            </a>
          </div>
        </div>
      `;
    });

    html += `</div>`;
  }

  html += `
    <div class="research-executive-card">
      <div class="research-executive-title">
        <span>📊 EXECUTIVE MARKET & PRICING REPORT</span>
      </div>
      <div class="markdown-body" style="color: #d8e6f7; line-height: 1.6;">
        ${typeof marked !== "undefined" ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(summaryMarkdown)) : escapeHtml(summaryMarkdown)) : summaryMarkdown}
      </div>
    </div>
  `;

  return html;
}

function renderDossierReport(summaryMarkdown, records, query) {
  if (isJobIntent(query || lastQueryText, records)) {
    return renderJobRadarUI(summaryMarkdown, records);
  } else if (isMarketIntent(query || lastQueryText, records)) {
    return renderMarketIntelUI(summaryMarkdown, records, query || lastQueryText);
  } else {
    return renderResearchDossierUI(summaryMarkdown, records, query || lastQueryText);
  }
}

window.switchResultsTab = function(tabName) {
  document.querySelectorAll(".results-tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".results-tab-content").forEach(c => c.classList.remove("active"));

  const btn = document.getElementById(`tab-btn-${tabName}`);
  const content = document.getElementById(`tab-content-${tabName}`);
  if (btn) btn.classList.add("active");
  if (content) content.classList.add("active");
};

function renderOnPageResults(summaryMarkdown, records, query, runData) {
  const panel = document.getElementById("center-results-panel");
  if (!panel) return;

  if (summaryMarkdown) latestSummaryMarkdown = summaryMarkdown;
  if (records && records.length > 0 && latestRunRecords.length === 0) latestRunRecords = records;

  const queryBadge = document.getElementById("results-panel-query-badge");
  const panelTitle = document.getElementById("results-panel-title");
  const tabBtnDossier = document.getElementById("tab-btn-dossier");
  const tabBtnEntities = document.getElementById("tab-btn-entities");
  const tabBtnSources = document.getElementById("tab-btn-sources");

  const dossierContainer = document.getElementById("onpage-dossier-markdown");
  const entitiesContainer = document.getElementById("onpage-entities-container");
  const sourcesContainer = document.getElementById("onpage-sources-container");

  const queryText = query || lastQueryText || "Research Directive";
  if (queryBadge) {
    queryBadge.textContent = `Directive: "${queryText}"`;
    queryBadge.title = queryText;
  }

  const validRecords = (records || []).filter(isSubstantiveRecord);
  const isJob = isJobIntent(queryText, records);
  const isMarket = !isJob && isMarketIntent(queryText, records);

  // 1. Configure Header & Tab Labels based on Mode
  if (isJob) {
    if (panelTitle) panelTitle.textContent = "🎯 0-SEC LIVE JOB RADAR & ATS DIRECTORY";
    if (tabBtnDossier) tabBtnDossier.innerHTML = `<span>⚡ ATS JOB RADAR (<span id="tab-jobs-count">${validRecords.length}</span>)</span>`;
    if (tabBtnEntities) tabBtnEntities.innerHTML = `<span>📑 EXECUTIVE SUMMARY</span>`;
    if (tabBtnSources) tabBtnSources.innerHTML = `<span>🌐 ATS PORTALS & CITATIONS (<span id="tab-sources-count">0</span>)</span>`;
  } else if (isMarket) {
    if (panelTitle) panelTitle.textContent = "📊 MARKET & COMPETITIVE INTELLIGENCE DOSSIER";
    if (tabBtnDossier) tabBtnDossier.innerHTML = `<span>📊 MARKET REPORT</span>`;
    if (tabBtnEntities) tabBtnEntities.innerHTML = `<span>🏷️ COMPETITOR MATRIX (<span id="tab-entities-count">${validRecords.length}</span>)</span>`;
    if (tabBtnSources) tabBtnSources.innerHTML = `<span>🌐 VERIFIED SOURCES (<span id="tab-sources-count">0</span>)</span>`;
  } else {
    if (panelTitle) panelTitle.textContent = "🔬 RESEARCH INTELLIGENCE DOSSIER";
    if (tabBtnDossier) tabBtnDossier.innerHTML = `<span>📖 EXECUTIVE DOSSIER</span>`;
    if (tabBtnEntities) tabBtnEntities.innerHTML = `<span>🧩 KNOWLEDGE MATRIX (<span id="tab-entities-count">${validRecords.length}</span>)</span>`;
    if (tabBtnSources) tabBtnSources.innerHTML = `<span>🌐 VERIFIED SOURCES (<span id="tab-sources-count">0</span>)</span>`;
  }

  // 2. Render Tab 1 & Tab 2 Contents
  if (isJob) {
    // Tab 1: Render Interactive ATS Job Radar Grid
    if (dossierContainer) {
      if (validRecords.length === 0) {
        dossierContainer.innerHTML = `
          <div style="padding: 36px; text-align: center; color: var(--text-muted); font-family: 'Share Tech Mono', monospace;">
            <div style="font-size: 32px; margin-bottom: 12px;">🎯</div>
            <div style="font-size: 14px; color: #fff; margin-bottom: 6px;">NO DIRECT ATS OPENINGS DETECTED FOR CURRENT QUERY</div>
            Try searching for broader roles like <em>"AI Engineer"</em>, <em>"Staff Python Roles"</em>, or <em>"ML Engineer"</em>.
          </div>
        `;
      } else {
        dossierContainer.innerHTML = `
          <div class="job-radar-results-header">
            <div class="job-radar-results-title">
              <span>⚡ DIRECT ATS VERIFIED OPENINGS</span>
              <span class="job-radar-results-count">${validRecords.length} OPPORTUNITIES LOCATED</span>
            </div>
            <div style="font-family: 'Share Tech Mono', monospace; font-size: 12px; color: var(--text-muted); margin-top: 4px;">
              Zero recruiter spam • High-precision radar filters applied • Direct Application Links
            </div>
          </div>
          <div class="job-card-grid">
            ${validRecords.map(rec => {
              const f = rec.fields || {};
              const title = f.title || f.name || "Open Role";
              const company = f.company || "Direct Employer";
              const location = f.location || "Remote / Unspecified";
              const salary = f.salary || null;
              const badge = f.freshness_badge || (f.posted_age_seconds !== undefined && f.posted_age_seconds < 60 ? "⚡ 0-SEC / JUST NOW" : (f.posted_at || "Recent"));
              const rawApplyUrl = f.application_url || rec.canonical_url || "#";
              const applyUrl = sanitizeUrl(rawApplyUrl);
              const isFresh = badge.includes("0-SEC") || badge.includes("JUST NOW") || badge.includes("s ago") || badge.includes("m ago") || badge.includes("Today");
              const confScore = Math.round((rec.confidence !== undefined && rec.confidence !== null ? rec.confidence : 0.95) * 100);

              return `
                <div class="job-card-item">
                  <div class="job-card-header">
                    <div>
                      <span class="job-card-title">${escapeHtml(title)}</span>
                      <span style="margin: 0 6px; color: var(--neon-cyan);">@</span>
                      <span class="job-card-company">${escapeHtml(company)}</span>
                    </div>
                    <span class="${isFresh ? 'zero-sec-badge' : 'phase-badge'}">${escapeHtml(badge)}</span>
                  </div>
                  <div class="job-card-meta-row">
                    <span>📍 ${escapeHtml(location)}</span>
                    ${salary ? `<span style="color: var(--neon-green); font-weight: 700;">💰 ${escapeHtml(salary)}</span>` : ''}
                    <span>🕒 Timestamp: ${escapeHtml(f.posted_at || 'Just now')}</span>
                    <span>🛡️ Confidence: ${confScore}%</span>
                  </div>
                  <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
                    <a href="${sanitizeUrl(applyUrl)}" target="_blank" rel="noopener noreferrer" class="direct-apply-btn" onclick="trackCockpitApply('${rec.id}', this, event)">
                      <span>APPLY DIRECTLY ON ATS</span>
                      <span>➔</span>
                    </a>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        `;
      }
    }

    // Tab 2: Render Executive Markdown Summary
    if (entitiesContainer) {
      entitiesContainer.className = "onpage-markdown-wrap";
      const rawMd = summaryMarkdown || `### Job Radar Synthesis\n\nAnalyzed **${validRecords.length}** live career opportunities across primary ATS portals.`;
      entitiesContainer.innerHTML = `
        <div class="markdown-body onpage-markdown-body" style="padding: 12px 6px; color: #d8e6f7; line-height: 1.7;">
          ${typeof marked !== "undefined" ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(rawMd)) : escapeHtml(rawMd)) : `<pre>${escapeHtml(rawMd)}</pre>`}
        </div>
      `;
    }
  } else if (isMarket) {
    // Market Mode: Tab 1 Market Dossier Markdown
    if (dossierContainer) {
      const rawMd = summaryMarkdown || `### Market Intelligence Report\n\nSynthesized competitive findings for "${queryText}".`;
      dossierContainer.innerHTML = typeof marked !== "undefined" ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(rawMd)) : escapeHtml(rawMd)) : `<pre>${escapeHtml(rawMd)}</pre>`;
    }

    // Market Mode: Tab 2 Competitor & Pricing Matrix Cards
    const tabEntitiesCount = document.getElementById("tab-entities-count");
    if (tabEntitiesCount) tabEntitiesCount.textContent = validRecords.length;

    if (entitiesContainer) {
      if (validRecords.length > 0) {
        entitiesContainer.innerHTML = `
          <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; padding: 12px 0;">
            ${validRecords.map(rec => {
              const f = rec.fields || {};
              const compName = f.company_name || f.product_name || f.name || "Vendor Solution";
              const pricingModel = f.pricing_model || "Tiered / Usage";
              const startingPrice = f.starting_price || "Free Tier Available";
              const target = f.target_audience || "Engineering Teams";
              const features = Array.isArray(f.key_features) ? f.key_features : (f.key_features ? [f.key_features] : []);
              const strengths = f.strengths || "";
              const url = f.website_url || rec.canonical_url || "#";
              const confScore = Math.round((rec.confidence !== undefined && rec.confidence !== null ? rec.confidence : 0.95) * 100);

              return `
                <div class="job-card-item" style="border-left: 3px solid var(--neon-cyan);">
                  <div class="job-card-header" style="margin-bottom: 8px;">
                    <span class="job-card-title" style="font-size: 15px; color: #fff;">${escapeHtml(compName)}</span>
                    <span class="phase-badge" style="background: rgba(0, 240, 255, 0.15); border-color: var(--neon-cyan); color: var(--neon-cyan); font-size: 11px;">
                      🏷️ ${escapeHtml(pricingModel)}
                    </span>
                  </div>
                  <div class="job-card-meta-row" style="margin-bottom: 8px; gap: 8px;">
                    <span style="color: var(--neon-green); font-weight: bold;">💰 ${escapeHtml(startingPrice)}</span>
                    <span>🎯 ${escapeHtml(target)}</span>
                    <span>🛡️ ${confScore}%</span>
                  </div>
                  ${features.length > 0 ? `
                    <div style="font-size: 12px; color: #b8cde6; margin-bottom: 8px; line-height: 1.4;">
                      <strong style="color: var(--neon-cyan); font-family: 'Share Tech Mono', monospace; font-size: 10px;">CORE CAPABILITIES:</strong>
                      <ul style="margin: 4px 0 0 16px; padding: 0;">
                        ${features.slice(0, 3).map(feat => `<li>${escapeHtml(feat)}</li>`).join("")}
                      </ul>
                    </div>
                  ` : ''}
                  ${strengths ? `
                    <div class="research-quote-box" style="margin: 6px 0; font-size: 11px; padding: 6px 8px;">
                      ⚡ <em>${escapeHtml(strengths)}</em>
                    </div>
                  ` : ''}
                  <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
                    <a href="${sanitizeUrl(url)}" target="_blank" rel="noopener noreferrer" class="direct-apply-btn" style="border-color: var(--neon-cyan); color: var(--neon-cyan);">
                      <span>VISIT SOLUTION ➔</span>
                    </a>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        `;
      } else {
        entitiesContainer.innerHTML = `
          <div style="padding: 24px; text-align: center; color: var(--text-muted); font-family: 'Share Tech Mono', monospace;">
            <div style="font-size: 24px; margin-bottom: 8px;">📊</div>
            Market synthesis compiled from primary web corpora.<br>
            View the <a href="javascript:void(0)" onclick="switchResultsTab('dossier')" style="color: var(--neon-cyan);">Market Report</a> tab for the complete competitor analysis.
          </div>
        `;
      }
    }
  } else {
    // Research Mode: Tab 1 Dossier Markdown
    if (dossierContainer) {
      const rawMd = summaryMarkdown || `### Research Complete\n\nSynthesized findings for "${queryText}".`;
      dossierContainer.innerHTML = typeof marked !== "undefined" ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(rawMd)) : escapeHtml(rawMd)) : `<pre>${escapeHtml(rawMd)}</pre>`;
    }

    // Research Mode: Tab 2 Knowledge Matrix / Entities
    const tabEntitiesCount = document.getElementById("tab-entities-count");
    if (tabEntitiesCount) tabEntitiesCount.textContent = validRecords.length;

    if (entitiesContainer) {
      if (validRecords.length > 0) {
        entitiesContainer.innerHTML = validRecords.map((rec, idx) => {
          const f = rec.fields || {};
          const entityName = f.name || f.title || f.concept || `Finding #${idx + 1}`;
          const cat = f.category || "Architecture & Core Concept";
          const confScore = Math.round((rec.confidence !== undefined && rec.confidence !== null ? rec.confidence : 0.95) * 100);
          const sourceUrl = rec.canonical_url || f.documentation_url || f.application_url || f.url || "#";
          let domain = "web source";
          try { domain = new URL(sourceUrl).hostname; } catch(e) {}

          let evidenceQuote = "";
          const ev = rec.evidence || f.evidence || f._evidence;
          if (Array.isArray(ev) && ev.length > 0) {
            evidenceQuote = ev[0].evidence_text || ev[0].quote || "";
          }
          if (!evidenceQuote && f.description) {
            evidenceQuote = f.description;
          }

          const tags = [];
          for (const [k, v] of Object.entries(f)) {
            if (!v) continue;
            const keyLow = k.toLowerCase();
            if (["name", "title", "concept", "description", "posted_at", "posted_age_seconds", "freshness_badge", "application_url", "documentation_url", "evidence"].includes(keyLow)) continue;
            let label = k.replace(/_/g, " ").toUpperCase();
            let displayVal = typeof v === "object" ? JSON.stringify(v) : String(v);
            if (displayVal.length > 38) displayVal = displayVal.substring(0, 35) + "...";
            tags.push({ label, val: displayVal });
          }

          return `
            <div class="research-entity-card">
              <div class="research-entity-top">
                <div class="research-entity-name">${escapeHtml(entityName)}</div>
                <div style="font-family: 'Share Tech Mono', monospace; font-size: 10px; color: var(--neon-green); background: rgba(0,255,163,0.1); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(0,255,163,0.3);">
                  CONFIDENCE: ${confScore}%
                </div>
              </div>
              <div style="font-family: 'Share Tech Mono', monospace; font-size: 11px; color: var(--neon-cyan); margin: 3px 0 6px 0;">
                CATEGORY: ${escapeHtml(cat)}
              </div>
              ${tags.length > 0 ? `
                <div class="research-tag-row">
                  ${tags.map(t => `<span class="research-tag-pill"><strong>${escapeHtml(t.label)}:</strong> ${escapeHtml(t.val)}</span>`).join("")}
                </div>
              ` : ''}
              ${evidenceQuote ? `
                <div class="research-quote-box">
                  "${escapeHtml(evidenceQuote)}"
                </div>
              ` : ''}
              <div class="research-card-actions">
                <span style="font-family: 'Share Tech Mono', monospace; font-size: 11px; color: var(--text-muted);">
                  Source: <strong>${escapeHtml(domain)}</strong>
                </span>
                <a href="${sanitizeUrl(sourceUrl)}" target="_blank" rel="noopener noreferrer" class="source-citation-btn">
                  <span>INSPECT CITATION ↗</span>
                </a>
              </div>
            </div>
          `;
        }).join("");
      } else {
        entitiesContainer.innerHTML = `
          <div style="padding: 24px; text-align: center; color: var(--text-muted); font-family: 'Share Tech Mono', monospace;">
            <div style="font-size: 24px; margin-bottom: 8px;">📖</div>
            Knowledge synthesis compiled from primary web corpora.<br>
            View the <a href="javascript:void(0)" onclick="switchResultsTab('dossier')" style="color: var(--neon-cyan);">Executive Dossier</a> tab for the complete teardown and analysis.
          </div>
        `;
      }
    }
  }

  // 3. Render Sources & Citations
  const sourceUrls = new Set();
  const sourcesList = [];
  (records || []).forEach(r => {
    const u = r.canonical_url || r.fields?.documentation_url || r.fields?.application_url || r.fields?.url;
    if (u && !sourceUrls.has(u)) {
      sourceUrls.add(u);
      let dom = u;
      try { dom = new URL(u).hostname; } catch(e) {}
      sourcesList.push({ url: u, domain: dom, snippet: r.fields?.description || (isJob ? "Direct applicant tracking career portal" : "Verified primary documentation source") });
    }
  });

  const displayCount = sourcesList.length > 0 ? sourcesList.length : (runData.pages_fetched || 1);
  const tabSourcesCountEl = document.getElementById("tab-sources-count");
  if (tabSourcesCountEl) tabSourcesCountEl.textContent = displayCount;

  if (sourcesContainer) {
    if (sourcesList.length > 0) {
      sourcesContainer.innerHTML = sourcesList.map(s => `
        <div class="research-source-card">
          <div class="research-source-top">
            <span class="research-source-title">🌐 ${escapeHtml(s.domain)}</span>
            <span class="phase-badge" style="border-color: var(--neon-cyan); color: var(--neon-cyan);">${isJob ? 'ATS PORTAL' : 'VERIFIED SOURCE'}</span>
          </div>
          <div class="research-source-snippet">${escapeHtml(s.snippet)}</div>
          <div style="display: flex; justify-content: flex-end;">
            <a href="${sanitizeUrl(s.url)}" target="_blank" rel="noopener noreferrer" class="source-citation-btn">
              <span>EXPLORE SOURCE URL ↗</span>
            </a>
          </div>
        </div>
      `).join("");
    } else {
      sourcesContainer.innerHTML = `
        <div style="padding: 20px; text-align: center; color: var(--text-muted); font-family: 'Share Tech Mono', monospace;">
          Ingested and verified across ${runData.pages_fetched || 1} public web documentation sources.
        </div>
      `;
    }
  }

  // 4. Update Download Links on the On-Page Panel
  if (runData && runData.run_id) {
    const dlMd = `/runs/${runData.run_id}/export?format=md`;
    const dlDocx = `/runs/${runData.run_id}/export?format=docx`;
    const dlJson = `/runs/${runData.run_id}/export?format=json`;

    const btnMd = document.getElementById("onpage-dl-md");
    const btnDocx = document.getElementById("onpage-dl-docx");
    const btnJson = document.getElementById("onpage-dl-json");
    const fsBtn = document.getElementById("onpage-fullscreen-btn");

    if (btnMd) { btnMd.href = dlMd; btnMd.style.display = currentAgentMode === "jobs" ? "none" : "inline-flex"; }
    if (btnDocx) { btnDocx.href = dlDocx; btnDocx.style.display = currentAgentMode === "jobs" ? "none" : "inline-flex"; }
    if (btnJson) btnJson.href = dlJson;

    if (fsBtn) {
      fsBtn.onclick = () => {
        const resultsModal = document.getElementById("results-modal");
        const modalReportContent = document.getElementById("modal-report-content");
        if (modalReportContent) modalReportContent.innerHTML = renderDossierReport(latestSummaryMarkdown, latestRunRecords, lastQueryText);
        if (resultsModal) resultsModal.style.display = "flex";
      };
    }
  }

  // Set default tab to dossier
  switchResultsTab("dossier");

  // Show the on-page panel directly on screen and minimize background prompt card
  const promptCard = document.getElementById("center-prompt-card");
  const dialoguePill = document.getElementById("bot-dialogue-pill");
  if (promptCard) promptCard.style.display = "none";
  if (dialoguePill) dialoguePill.style.display = "none";
  panel.style.display = "flex";
}

document.addEventListener("DOMContentLoaded", () => {
  try {
    if (typeof AIBotAvatar !== "undefined") {
      scene = new AIBotAvatar("webgl-canvas", { enableControls: true });
      console.log("[DataHunt] High-fidelity AIBotAvatar engine active");
    } else if (typeof CyberneticAgentScene !== "undefined") {
      scene = new CyberneticAgentScene("webgl-canvas");
    }
  } catch (e) {
    console.warn("Avatar scene init warning:", e);
  }

  try {
    hud = new HUDController();
  } catch (e) {
    console.warn("HUDController init warning:", e);
  }

  // Elements
  const queryInput = document.getElementById("query-input");
  const engageBtn = document.getElementById("engage-btn");
  const promptForm = document.getElementById("prompt-form");
  const promptInputRow = document.getElementById("prompt-input-row");
  const analyzingBanner = document.getElementById("analyzing-banner");
  const analyzingTitle = document.getElementById("analyzing-title");
  const analyzingSubtitle = document.getElementById("analyzing-subtitle");
  const cardStateLabel = document.getElementById("card-state-label");
  const cardStatePill = document.getElementById("card-state-pill");
  const viewResultsBtn = document.getElementById("view-results-btn");
  const tickerText = document.getElementById("ticker-text");

  const statPages = document.getElementById("stat-pages");
  const statQueries = document.getElementById("stat-queries");
  const statVerified = document.getElementById("stat-verified");
  const statRejected = document.getElementById("stat-rejected");

  const downloadBtn = document.getElementById("download-btn");
  const exportDesc = document.getElementById("export-status-desc");

  // Apply Agent Mode UI (Research / Jobs / Market)
  configureAgentModeUI(currentAgentMode);

  // Auto-load query from URL parameter if dispatched from Agent Hub
  const incomingQuery = urlParams.get("q");
  if (incomingQuery && queryInput) {
    queryInput.value = incomingQuery;
    setTimeout(() => {
      if (typeof window.triggerRun === "function") {
        window.triggerRun();
      }
    }, 700);
  }

  // Audio Mute Toggle Button
  const audioToggleBtn = document.getElementById("audio-toggle-btn");
  const audioToggleIcon = document.getElementById("audio-toggle-icon");
  const audioToggleText = document.getElementById("audio-toggle-text");

  function updateAudioUI() {
    if (!window.SoundFX) return;
    const muted = window.SoundFX.isMuted;
    if (audioToggleIcon) audioToggleIcon.textContent = muted ? "🔇" : "🔊";
    if (audioToggleText) audioToggleText.textContent = muted ? "MUTED" : "AUDIO ON";
    if (audioToggleBtn) {
      audioToggleBtn.style.opacity = muted ? "0.6" : "1.0";
      audioToggleBtn.style.borderColor = muted ? "rgba(255,255,255,0.3)" : "var(--neon-cyan)";
      audioToggleBtn.style.color = muted ? "var(--text-muted)" : "var(--neon-cyan)";
    }
  }

  if (audioToggleBtn) {
    updateAudioUI();
    audioToggleBtn.addEventListener("click", () => {
      if (window.SoundFX) {
        window.SoundFX.toggleMute();
        updateAudioUI();
        if (!window.SoundFX.isMuted) {
          window.SoundFX.playClick();
        }
      }
    });
  }

  // Modal Elements
  const resultsModal = document.getElementById("results-modal");
  const modalReportContent = document.getElementById("modal-report-content");
  const closeModalBtn = document.getElementById("close-modal-btn");
  const modalDownloadBtn = document.getElementById("modal-download-btn");
  const newQueryBtn = document.getElementById("new-query-btn");

  // Close Modal Handlers
  if (closeModalBtn) {
    closeModalBtn.addEventListener("click", () => {
      if (resultsModal) resultsModal.style.display = "none";
    });
  }
  if (resultsModal) {
    resultsModal.addEventListener("click", (e) => {
      if (e.target === resultsModal) resultsModal.style.display = "none";
    });
  }
  if (viewResultsBtn) {
    viewResultsBtn.addEventListener("click", () => {
      const panel = document.getElementById("center-results-panel");
      const promptCard = document.getElementById("center-prompt-card");
      const dialoguePill = document.getElementById("bot-dialogue-pill");
      if (promptCard) promptCard.style.display = "none";
      if (dialoguePill) dialoguePill.style.display = "none";
      if (panel) {
        panel.style.display = "flex";
        if (typeof window.switchResultsTab === "function") window.switchResultsTab("dossier");
      } else if (resultsModal && (latestSummaryMarkdown || latestRunRecords.length > 0)) {
        modalReportContent.innerHTML = renderDossierReport(latestSummaryMarkdown, latestRunRecords, lastQueryText);
        resultsModal.style.display = "flex";
      }
    });
  }

  const onpageCloseBtn = document.getElementById("onpage-close-btn");
  if (onpageCloseBtn) {
    onpageCloseBtn.addEventListener("click", () => {
      const panel = document.getElementById("center-results-panel");
      const promptCard = document.getElementById("center-prompt-card");
      const dialoguePill = document.getElementById("bot-dialogue-pill");
      if (panel) panel.style.display = "none";
      if (promptCard) promptCard.style.display = "flex";
      if (dialoguePill) dialoguePill.style.display = "flex";
      const banner = document.getElementById("analyzing-banner");
      if (banner) banner.style.display = "flex";
      if (viewResultsBtn) {
        viewResultsBtn.style.display = "inline-flex";
        viewResultsBtn.textContent = "VIEW REPORT";
      }
    });
  }

  if (newQueryBtn) {
    newQueryBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      window.resetToInputMode();
    });
  }

  // Freshness Radar Filter Buttons
  const chip0sec = document.getElementById("freshness-chip-0sec");
  const chip24h = document.getElementById("freshness-chip-24h");
  const chipAll = document.getElementById("freshness-chip-all");
  if (chip0sec) chip0sec.addEventListener("click", () => window.setFreshness("0sec"));
  if (chip24h) chip24h.addEventListener("click", () => window.setFreshness("24h"));
  if (chipAll) chipAll.addEventListener("click", () => window.setFreshness("all"));

  // Multi-Model Neural Core Buttons
  const modelAuto = document.getElementById("model-chip-auto");
  const modelLite = document.getElementById("model-chip-lite");
  const modelFlash = document.getElementById("model-chip-flash");
  if (modelAuto) modelAuto.addEventListener("click", () => window.setModel("auto"));
  if (modelLite) modelLite.addEventListener("click", () => window.setModel("lite"));
  if (modelFlash) modelFlash.addEventListener("click", () => window.setModel("flash"));

  // Query Presets Container (delegated click handling)
  const presetsContainer = document.getElementById("dynamic-presets-container");
  if (presetsContainer) {
    presetsContainer.addEventListener("click", (e) => {
      const chip = e.target.closest(".preset-chip");
      if (chip) {
        const text = chip.getAttribute("data-query") || chip.textContent.trim();
        window.setQuery(text);
      }
    });
  }

  // WebSocket
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/agent-stream`;
  socket = null;

  let wsReconnectAttempts = 0;
  function connectWebSocket() {
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      wsReconnectAttempts = 0;
      hud.appendLog("SOCKET", "Neural link synchronized with DataHunt backend core.");
    };

    socket.onclose = () => {
      const delay = Math.min(1000 * Math.pow(2, wsReconnectAttempts), 30000);
      wsReconnectAttempts++;
      setTimeout(connectWebSocket, delay);
    };

    socket.onerror = function(e) {
      console.error('WebSocket error:', e);
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleAgentEvent(msg);
      } catch (e) {
        console.error("Failed to parse websocket message", e);
      }
    };
  }

  connectWebSocket();

  // Restore latest session state from backend on page refresh
  async function restoreLastRunState() {
    const incomingQuery = urlParams.get("q");
    if (incomingQuery) return; // Dedicated new query from query param, do not overwrite

    try {
      const res = await fetch("/runs/latest");
      if (!res.ok) return;
      const run = await res.json();
      if (!run || !run.id) return;

      console.log("[DataHunt] Restoring previous session state from backend:", run);

      if (queryInput && !queryInput.value && run.query) {
        queryInput.value = run.query;
      }
      lastQueryText = run.query || lastQueryText;

      // Restore Telemetry Counters
      if (run.counters) {
        updateCounters(run.counters);
      }

      if (run.status === "completed") {
        latestSummaryMarkdown = run.summary || "";
        if (cardStateLabel) cardStateLabel.textContent = "COMPLETED";

        // Fetch records for this completed run
        const recRes = await fetch(`/runs/${run.id}/records`);
        if (recRes.ok) {
          const records = await recRes.json();
          latestRunRecords = records || [];

          if (latestRunRecords.length > 0) {
            const confList = latestRunRecords
              .map(r => typeof r.confidence === "number" ? r.confidence : null)
              .filter(c => c !== null);
            if (confList.length > 0 && hud && typeof hud.updateConfidence === "function") {
              const avg = Math.round((confList.reduce((a, b) => a + b, 0) / confList.length) * 100);
              hud.updateConfidence(avg);
            }
          }

          // Render on-page panel and report
          renderOnPageResults(latestSummaryMarkdown, latestRunRecords, run.query, {
            run_id: run.id,
            pages_fetched: run.counters?.pages_fetched || 1,
            records_verified: run.counters?.records_verified || latestRunRecords.length,
            export_file: run.exports?.[0]?.file_path
          });

          // Show banner controls so user can toggle report or new query
          if (viewResultsBtn) viewResultsBtn.style.display = "block";
          if (newQueryBtn) newQueryBtn.style.display = "block";
          if (promptInputRow) promptInputRow.style.display = "none";
          if (analyzingBanner) analyzingBanner.style.display = "flex";
          if (analyzingTitle) analyzingTitle.textContent = "Mission complete - all records verified!";
          if (analyzingSubtitle) analyzingSubtitle.textContent = `Query: "${run.query}"`;
          const analyzingSpinner = document.getElementById("analyzing-spinner");
          if (analyzingSpinner) analyzingSpinner.classList.add("completed");
        }

        if (downloadBtn && run.exports?.length > 0) {
          const exp = run.exports[0];
          downloadBtn.href = `/exports/${exp.id}`;
          downloadBtn.style.display = "flex";
          if (exportDesc) exportDesc.textContent = `Export ready: ${exp.export_format?.toUpperCase()}`;
        }

        if (hud && typeof hud.appendLog === "function") {
          hud.appendLog("COMPLETE", `Session state restored from backend: ${run.counters?.records_verified || 0} records.`);
        }
      } else if (run.status === "in_progress") {
        isRunning = true;
        if (promptInputRow) promptInputRow.style.display = "none";
        if (analyzingBanner) analyzingBanner.style.display = "flex";
        if (analyzingTitle) analyzingTitle.textContent = "Autonomous Agent actively executing...";
        if (analyzingSubtitle) analyzingSubtitle.textContent = `Query: "${run.query}"`;
        if (cardStateLabel) cardStateLabel.textContent = "ENGAGED";
        if (scene && typeof scene.setAgentState === "function") scene.setAgentState("SEARCHING");
        if (hud && typeof hud.appendLog === "function") {
          hud.appendLog("RECONNECT", `Reconnected to active in-flight run: "${run.query}"`);
        }
      }
    } catch (e) {
      console.warn("[DataHunt] Could not restore previous session state:", e);
    }
  }

  // Trigger restore immediately on load
  restoreLastRunState();

  function handleAgentEvent(msg) {
    const { event, data } = msg;

    if (event === "model.active") {
      const label = data.label || data.model;
      hud.appendLog("MODEL", `Neural Core: ${label}`);
      const modelTag = document.getElementById("active-model-tag");
      if (modelTag) {
        if (data.mode === "auto") {
          modelTag.textContent = "🔄 MULTI-MODEL";
        } else if (data.mode === "lite" || (data.model && data.model.includes("lite"))) {
          modelTag.textContent = "⚡ LITE";
        } else {
          modelTag.textContent = "🚀 3.8 FLASH";
        }
      }
    }
    else if (event === "phase.change") {
      scene.setAgentState(data.phase);
      hud.setPhaseState(data.phase);
      hud.setWaveformActivity(0.9);

      cardStateLabel.textContent = `PHASE: ${data.phase}`;
      analyzingTitle.textContent = `${data.phase}...`;
      analyzingSubtitle.textContent = data.message || "Synthesizing research telemetry";
      tickerText.textContent = `[${data.phase}] ${data.message || ""}`;

      hud.appendLog("PHASE", `${data.phase} - ${data.message || ""}`);
      updateCounters(data.counters);
    }
    else if (event === "plan.ready") {
      if (scene && typeof scene.setAgentState === "function") scene.setAgentState("PLANNING");
      hud.appendLog("PLAN", `Generated ${data.count} targeted queries.`);
      tickerText.textContent = `Formulated ${data.count} queries: "${data.queries[0]?.query || 'Targeted search'}"`;
    }
    else if (event === "search.result") {
      if (scene && typeof scene.onSearchResult === "function") scene.onSearchResult(data.query, data.results_count);
      hud.appendLog("SEARCH", `Query: "${data.query}" -> ${data.results_count} results`, data.duration_ms);
      tickerText.textContent = `Searching: "${data.query}" (${data.results_count} hits)`;
      updateCounters(data.counters);
    }
    else if (event === "page.fetched") {
      if (scene && typeof scene.onPageFetched === "function") scene.onPageFetched(data.url, data.status_code);
      hud.appendLog("FETCH", `Status ${data.status_code} from ${data.url}`, data.duration_ms);
      tickerText.textContent = `Ingesting: ${data.url}`;
      updateCounters(data.counters);
    }
    else if (event === "page.failed") {
      if (scene && typeof scene.onPageFetched === "function") scene.onPageFetched(data.url, 403);
      hud.appendLog("BLOCKED", `Access denied to ${data.url}`);
      updateCounters(data.counters);
    }
    else if (event === "record.extracted") {
      const entity = data.fields?.name || data.fields?.title || "Discovered Entity";
      if (scene && typeof scene.onRecordExtracted === "function") scene.onRecordExtracted(entity);
      hud.appendLog("EXTRACT", `Candidate: ${entity}`);
      tickerText.textContent = `Extracted entity: ${entity}`;
      updateCounters(data.counters);
    }
    else if (event === "record.verified") {
      const entity = data.fields?.name || data.fields?.title || "Entity";
      const confVal = typeof data.confidence === "number" ? data.confidence : 0.95;
      if (scene && typeof scene.onRecordVerified === "function") scene.onRecordVerified(entity, data.status, confVal);
      hud.appendLog("VERIFY", `[${data.status}] ${entity} (conf: ${(confVal * 100).toFixed(0)}%)`);
      if (data.status === "VERIFIED") {
        runConfidenceSum += confVal;
        runConfidenceCount += 1;
        const runningAvg = Math.round((runConfidenceSum / runConfidenceCount) * 100);
        if (hud && typeof hud.updateConfidence === "function") {
          hud.updateConfidence(runningAvg);
        }
        tickerText.textContent = `Verified: ${entity} (Confidence ${(confVal * 100).toFixed(0)}%)`;
      }
      updateCounters(data.counters);
    }
    else if (event === "run.completed") {
      isRunning = false;
      scene.setAgentState("COMPLETED");
      hud.setPhaseState("EXPORTING");
      hud.setWaveformActivity(0.3);

      cardStateLabel.textContent = "MISSION COMPLETE";
      analyzingTitle.textContent = "Research Complete";
      if (data.records_verified > 0) {
        analyzingSubtitle.textContent = `Verified ${data.records_verified} records • Dataset exported`;
        tickerText.textContent = `Mission Complete. Verified ${data.records_verified} records.`;
      } else {
        analyzingSubtitle.textContent = `Research synthesized across ${data.pages_fetched || 1} sources • Dossier ready`;
        tickerText.textContent = `Mission Complete. Research synthesized across ${data.pages_fetched || 1} sources.`;
      }
      
      // Stop the spinning circle animation and show completed checkmark
      const analyzingSpinner = document.getElementById("analyzing-spinner");
      if (analyzingSpinner) {
        analyzingSpinner.classList.remove("failed");
        analyzingSpinner.classList.add("completed");
      }

      // Update confidence gauge to final average verification confidence
      let finalConfidence = 0;
      if (typeof data.confidence === "number" && data.confidence > 0) {
        finalConfidence = Math.round(data.confidence * 100);
      } else if (runConfidenceCount > 0) {
        finalConfidence = Math.round((runConfidenceSum / runConfidenceCount) * 100);
      } else if (data.records_verified > 0 || (data.pages_fetched && data.pages_fetched > 0)) {
        finalConfidence = 95;
      }
      if (hud && typeof hud.updateConfidence === "function" && finalConfidence > 0) {
        hud.updateConfidence(finalConfidence);
      }

      // Ensure telemetry stat tiles display final verified records and pages counts
      if (data.records_verified !== undefined && statVerified) {
        statVerified.textContent = data.records_verified;
      }
      if (data.pages_fetched !== undefined && statPages) {
        statPages.textContent = data.pages_fetched;
      }

      // Store summary for modal view
      latestSummaryMarkdown = data.summary || `### Mission Complete\n\n- Verified Records: **${data.records_verified}**\n- Pages Fetched: **${data.pages_fetched}**\n\nDataset is available for download.`;
      
      // Show View Report and New Query buttons right inside the banner!
      viewResultsBtn.style.display = "block";
      if (newQueryBtn) newQueryBtn.style.display = "block";

      if (data.records_verified > 0) {
        hud.appendLog("COMPLETE", `Mission complete: Verified ${data.records_verified} records (Confidence ${finalConfidence}%).`);
      } else {
        hud.appendLog("COMPLETE", `Mission complete: Synthesized technical dossier across ${data.pages_fetched || 1} sources.`);
      }

      // Update download links for multi-format access (.md, .docx, .json)
      if (data.run_id) {
        const dlMd = `/runs/${data.run_id}/export?format=md`;
        const dlDocx = `/runs/${data.run_id}/export?format=docx`;
        const dlJson = `/runs/${data.run_id}/export?format=json`;

        exportDesc.textContent = `Ready: ${data.export_file || 'Research Export'}`;
        downloadBtn.style.display = "flex";

        if (currentAgentMode === "research") {
          downloadBtn.href = dlMd;
          downloadBtn.innerHTML = `<span>📄 DOWNLOAD DOSSIER (.MD)</span>`;
        } else {
          downloadBtn.href = dlJson;
          downloadBtn.innerHTML = `<span>📥 DOWNLOAD DATASET</span>`;
        }

        const btnMd = document.getElementById("modal-dl-md");
        const btnDocx = document.getElementById("modal-dl-docx");
        const btnJson = document.getElementById("modal-dl-json");

        if (btnMd) btnMd.href = dlMd;
        if (btnDocx) btnDocx.href = dlDocx;
        if (btnJson) btnJson.href = dlJson;

        // In jobs mode, hide the .md/.docx buttons and emphasize JSON
        if (currentAgentMode === "jobs") {
          if (btnMd) btnMd.style.display = "none";
          if (btnDocx) btnDocx.style.display = "none";
          if (btnJson) btnJson.innerHTML = "<span>📥 DOWNLOAD JOBS (JSON)</span>";
        } else {
          if (btnMd) btnMd.style.display = "flex";
          if (btnDocx) btnDocx.style.display = "flex";
          if (btnJson) btnJson.innerHTML = "<span>📦 RAW DATA (.JSON)</span>";
        }
      }

      // 1. Immediately render on-page research dossier panel!
      renderOnPageResults(latestSummaryMarkdown, latestRunRecords, lastQueryText, data);

      // 2. Fetch full records to populate Knowledge Matrix and sync modal
      if (data.run_id) {
        fetch(`/runs/${data.run_id}/records`)
          .then(res => res.json())
          .then(records => {
            latestRunRecords = records || [];
            if (latestRunRecords.length > 0) {
              const confList = latestRunRecords
                .map(r => typeof r.confidence === "number" ? r.confidence : null)
                .filter(c => c !== null);
              if (confList.length > 0) {
                const avg = Math.round((confList.reduce((a, b) => a + b, 0) / confList.length) * 100);
                if (hud && typeof hud.updateConfidence === "function") {
                  hud.updateConfidence(avg);
                }
              }
            }
            renderOnPageResults(latestSummaryMarkdown, latestRunRecords, lastQueryText, data);
            if (modalReportContent) {
              modalReportContent.innerHTML = renderDossierReport(latestSummaryMarkdown, latestRunRecords, lastQueryText);
            }
          })
          .catch(() => {
            renderOnPageResults(latestSummaryMarkdown, latestRunRecords, lastQueryText, data);
          });
      }
    }
    else if (event === "run.failed") {
      isRunning = false;
      if (promptInputRow) promptInputRow.style.display = "flex";
      if (analyzingBanner) analyzingBanner.style.display = "none";
      if (scene && typeof scene.setAgentState === "function") scene.setAgentState("STANDBY");
      if (cardStateLabel) cardStateLabel.textContent = "ABORTED";
      
      const errMsg = data.error || "Operation failed";
      if (tickerText) tickerText.textContent = `Directive halted: ${errMsg}`;
      if (hud) hud.appendLog("ERROR", `Directive failed: ${errMsg}`);

      // Show informative on-page error state so user knows why it failed
      const panel = document.getElementById("center-results-panel");
      const dossierContainer = document.getElementById("onpage-dossier-markdown");
      if (panel && dossierContainer) {
        dossierContainer.innerHTML = `
          <div style="background: rgba(255, 51, 102, 0.1); border: 1px solid var(--neon-red); border-radius: 8px; padding: 18px; margin-top: 10px;">
            <h3 style="color: var(--neon-red); margin-top: 0;">⚠️ Mission Directive Interrupted</h3>
            <p style="color: #fca5a5;">${escapeHtml(errMsg)}</p>
            <div style="margin-top: 14px;">
              <button onclick="window.resetToInputMode && window.resetToInputMode()" class="panel-action-btn new-query-action">
                <span>🔄 RETRY DIRECTIVE</span>
              </button>
            </div>
          </div>
        `;
        panel.style.display = "flex";
      }
    }
  }

  function updateCounters(counters) {
    if (!counters) return;
    if (statPages && counters.pages_fetched !== undefined) statPages.textContent = counters.pages_fetched;
    if (statQueries && counters.search_queries !== undefined) statQueries.textContent = counters.search_queries;
    if (statVerified && counters.records_verified !== undefined) statVerified.textContent = counters.records_verified;
    if (statRejected && counters.records_rejected !== undefined) {
      statRejected.textContent = (counters.records_rejected || 0) + (counters.records_duplicate || 0);
    }
  }

  // Trigger Research Run
  async function triggerRun() {
    console.log("[DataHunt] triggerRun invoked");
    if (isRunning) {
      console.log("[DataHunt] Task already running, ignoring duplicate trigger");
      return;
    }

    const queryInput = document.getElementById("query-input");
    let query = queryInput ? queryInput.value.trim() : "";
    if (!query) {
      query = (queryInput && queryInput.placeholder) 
        ? queryInput.placeholder.trim() 
        : "Research the latest developments in autonomous AI agents";
      if (queryInput) queryInput.value = query;
    }

    isRunning = true;
    lastQueryText = query;
    latestRunRecords = [];
    runConfidenceSum = 0;
    runConfidenceCount = 0;

    const engageBtnText = document.getElementById("engage-btn-text");
    if (engageBtnText) engageBtnText.textContent = "Sending...";
    if (downloadBtn) downloadBtn.style.display = "none";
    if (viewResultsBtn) viewResultsBtn.style.display = "none";
    if (exportDesc) exportDesc.textContent = "Synthesizing...";
    const centerResultsPanel = document.getElementById("center-results-panel");
    if (centerResultsPanel) centerResultsPanel.style.display = "none";

    // Collapse prompt input row into Sleek Analyzing Banner (matching video 00:07)
    if (promptInputRow) promptInputRow.style.display = "none";
    if (analyzingBanner) analyzingBanner.style.display = "flex";
    const analyzingSpinner = document.getElementById("analyzing-spinner");
    if (analyzingSpinner) {
      analyzingSpinner.classList.remove("completed", "failed");
    }
    if (analyzingTitle) analyzingTitle.textContent = "Analyzing request...";
    if (analyzingSubtitle) analyzingSubtitle.textContent = `Query: "${query}"`;
    if (cardStateLabel) cardStateLabel.textContent = "ENGAGED";
    if (tickerText) tickerText.textContent = `Analyzing: "${query}"`;

    if (scene && typeof scene.setAgentState === "function") {
      scene.setAgentState("PLANNING");
    }
    if (hud) {
      if (typeof hud.setPhaseState === "function") hud.setPhaseState("PLANNING");
      if (typeof hud.setWaveformActivity === "function") hud.setWaveformActivity(0.95);
      if (typeof hud.updateConfidence === "function") hud.updateConfidence(0);
      if (typeof hud.appendLog === "function") hud.appendLog("USER", `Target Query: "${query}"`);
    }

    updateCounters({ pages_fetched: 0, search_queries: 0, records_verified: 0, records_rejected: 0 });

    // Auto-detect agent mode based on query keywords
    const qLower = (query || "").toLowerCase();
    const isJobQuery = /\b(job|jobs|hiring|hire|career|careers|intern|internship|vacancy|vacancies|opening|openings|engineer|developer|analyst|salary|apply for|open role|job posting|job listing)\b/i.test(qLower);
    const isMarketQuery = !isJobQuery && /\b(pricing|competitor|market share|saas alternative|vs |pricing tier)\b/i.test(qLower);

    if (isJobQuery && currentAgentMode !== "jobs") {
      console.log("[DataHunt] Auto-switching agent mode to 'jobs' based on query keywords");
      currentAgentMode = "jobs";
      const agentModeBtn = document.getElementById("agentModeBtn");
      if (agentModeBtn) {
        agentModeBtn.innerHTML = "🎯 JOB RADAR";
        agentModeBtn.className = "cyber-btn badge-btn active-mode";
      }
    } else if (isMarketQuery && currentAgentMode !== "market") {
      console.log("[DataHunt] Auto-switching agent mode to 'market' based on query keywords");
      currentAgentMode = "market";
      const agentModeBtn = document.getElementById("agentModeBtn");
      if (agentModeBtn) {
        agentModeBtn.innerHTML = "📊 MARKET INTEL";
        agentModeBtn.className = "cyber-btn badge-btn active-mode";
      }
    }

    const freshnessDays = currentFreshness === "all" ? 30 : 1;

    // 1. Try WebSocket if connected
    if (socket && socket.readyState === WebSocket.OPEN) {
      console.log("[DataHunt] Dispatching research query via active WebSocket with freshness:", freshnessDays, "mode:", currentAgentMode);
      try {
        socket.send(JSON.stringify({
          action: "start_run",
          query: query,
          max_records: 150,
          freshness_days: freshnessDays,
          output_format: currentAgentMode === "research" ? "md" : "json",
          model: currentModel,
          agent_mode: currentAgentMode
        }));
        return;
      } catch (wsErr) {
        console.warn("[DataHunt] WebSocket dispatch failed, falling back to HTTP:", wsErr);
      }
    }

    // 2. High-reliability HTTP Fallback if WebSocket is not yet connected
    console.log("[DataHunt] WebSocket unavailable, invoking POST /tasks API directly with freshness:", freshnessDays, "model:", currentModel, "mode:", currentAgentMode);
    if (hud && typeof hud.appendLog === "function") {
      hud.appendLog("SYSTEM", `Dispatching via HTTP API (Freshness: ${currentFreshness.toUpperCase()}, Model: ${currentModel.toUpperCase()}, Agent: ${currentAgentMode.toUpperCase()})...`);
    }

    try {
      const response = await fetch("/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: query,
          max_records: 150,
          freshness_days: freshnessDays,
          output_format: currentAgentMode === "research" ? "md" : "json",
          model: currentModel,
          agent_mode: currentAgentMode,
          run_in_background: false
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      console.log("[DataHunt] HTTP run result:", result);

      handleAgentEvent({
        event: "run.completed",
        data: {
          task_id: result.task_id,
          run_id: result.run_id,
          status: result.status,
          records_verified: result.records_verified,
          records_rejected: result.records_rejected,
          confidence: result.confidence,
          model: result.model,
          model_mode: result.model_mode,
          export_file: result.export_file,
          summary: result.summary,
          pages_fetched: 1
        }
      });
    } catch (err) {
      console.error("[DataHunt] Run execution error:", err);
      handleAgentEvent({
        event: "run.failed",
        data: { error: err.message || "Failed to execute research task" }
      });
    }
  }

  // Expose to window globally
  window.triggerRun = triggerRun;

  // Bind Form Submit (handles both submit button click and Enter key in input)
  if (promptForm) {
    promptForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (window.SoundFX) window.SoundFX.playClick();
      triggerRun();
    });
  }
});
