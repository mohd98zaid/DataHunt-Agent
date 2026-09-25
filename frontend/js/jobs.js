// DataHunt Job Pipeline Tracker Controller

let allJobs = [];
let filteredJobs = [];
let activeFilter = "all";
let activeJobId = null;
let selectedJobIds = new Set();

// Multi-select filter state sets
const selectedCompanies = new Set();
const selectedLocations = new Set();
const selectedDates = new Set();
const selectedInterviewStages = new Set();

document.addEventListener("DOMContentLoaded", () => {
  initTracker();
});

async function initTracker() {
  setupEventListeners();
  setupMultiSelects();
  await loadJobs();
}

function formatLocationDisplay(rawLoc) {
  if (!rawLoc) return "Remote / Unspecified";
  let str = String(rawLoc).trim();
  if (!str || str.toLowerCase() === "unspecified") return "Remote / Unspecified";

  // If string contains JSON or dict patterns like {'@type': 'Place', ...}
  if (str.startsWith("{") || str.includes("@type") || str.includes("addressLocality") || str.includes("addressCountry")) {
    try {
      const jsonStr = str
        .replace(/'/g, '"')
        .replace(/True/g, 'true')
        .replace(/False/g, 'false')
        .replace(/None/g, 'null');
      const parsed = JSON.parse(jsonStr);
      const addr = parsed.address || parsed;
      const parts = [
        addr.addressLocality,
        addr.addressRegion,
        addr.addressCountry
      ].map(p => (p && typeof p === 'string') ? p.trim() : "").filter(Boolean);
      if (parts.length > 0) return parts.join(", ");
    } catch (e) {
      // Regex extraction fallback
      const locMatch = str.match(/addressLocality['"]\s*:\s*['"]([^'"]+)['"]/i);
      const regMatch = str.match(/addressRegion['"]\s*:\s*['"]([^'"]+)['"]/i);
      const countryMatch = str.match(/addressCountry['"]\s*:\s*['"]([^'"]+)['"]/i);
      const parts = [locMatch?.[1], regMatch?.[1], countryMatch?.[1]].map(p => p ? p.trim() : "").filter(Boolean);
      if (parts.length > 0) return parts.join(", ");
    }
  }

  // Strip leading/trailing braces, brackets, or quotes
  str = str.replace(/^['"{}\[\]\s]+|['"{}\[\]\s]+$/g, "");
  return str || "Remote / Unspecified";
}

function setupEventListeners() {
  const refreshBtn = document.getElementById("refresh-jobs-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadJobs());
  }

  const searchInput = document.getElementById("job-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", () => applyFilters());
  }

  // Filter chips (status)
  const filterChips = document.querySelectorAll(".filter-chip");
  filterChips.forEach(chip => {
    chip.addEventListener("click", () => {
      filterChips.forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      activeFilter = chip.getAttribute("data-filter");
      applyFilters();
    });
  });

  // Reset Filters Button
  const resetFiltersBtn = document.getElementById("reset-filters-btn");
  if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener("click", () => {
      resetAllFilters();
    });
  }

  // Select all visible jobs checkbox
  const selectAllCheckbox = document.getElementById("select-all-jobs");
  if (selectAllCheckbox) {
    selectAllCheckbox.addEventListener("change", (e) => {
      toggleSelectAll(e.target.checked);
    });
  }

  // Bulk Delete Button
  const bulkDeleteBtn = document.getElementById("bulk-delete-btn");
  if (bulkDeleteBtn) {
    bulkDeleteBtn.addEventListener("click", () => {
      bulkDeleteJobs();
    });
  }

  // Modal handlers
  const modal = document.getElementById("job-detail-modal");
  const closeBtn = document.getElementById("modal-close-btn");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      if (modal) modal.style.display = "none";
    });
  }

  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.style.display = "none";
    });
  }

  const saveStatusBtn = document.getElementById("modal-save-status-btn");
  if (saveStatusBtn) {
    saveStatusBtn.addEventListener("click", async () => {
      await saveModalStatus();
    });
  }

  const modalDeleteBtn = document.getElementById("modal-delete-btn");
  if (modalDeleteBtn) {
    modalDeleteBtn.addEventListener("click", async () => {
      if (activeJobId) {
        await deleteJob(activeJobId);
      }
    });
  }
}

function getDropdownConfig(type) {
  const meta = {
    company: { id: "company", pluralId: "companies", set: selectedCompanies, defaultLabel: "All Companies", unit: "Companies" },
    location: { id: "location", pluralId: "locations", set: selectedLocations, defaultLabel: "All Locations", unit: "Locations" },
    date: { id: "date", pluralId: "date", set: selectedDates, defaultLabel: "Any Scraped Time", unit: "Timeframes" },
    interview: { id: "interview", pluralId: "interview", set: selectedInterviewStages, defaultLabel: "All Call Stages", unit: "Stages" }
  };
  return meta[type];
}

function setupMultiSelects() {
  const types = ["company", "location", "date", "interview"];

  types.forEach(type => {
    const cfg = getDropdownConfig(type);
    const dropdown = document.getElementById(`dropdown-${cfg.id}`);
    const trigger = document.getElementById(`trigger-${cfg.id}`);
    const searchInput = document.getElementById(`search-${cfg.id}`);
    const selectAllBtn = document.getElementById(`select-all-${cfg.pluralId}`) || document.getElementById(`select-all-${cfg.id}`);
    const clearBtn = document.getElementById(`clear-${cfg.pluralId}`) || document.getElementById(`clear-${cfg.id}`);

    if (trigger && dropdown) {
      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = dropdown.classList.contains("open");
        document.querySelectorAll(".multi-select-dropdown").forEach(d => {
          if (d !== dropdown) d.classList.remove("open");
        });
        dropdown.classList.toggle("open", !isOpen);
        if (!isOpen && searchInput) {
          setTimeout(() => searchInput.focus(), 50);
        }
      });
    }

    if (searchInput) {
      searchInput.addEventListener("click", (e) => e.stopPropagation());
      searchInput.addEventListener("keydown", (e) => e.stopPropagation());
      searchInput.addEventListener("input", (e) => {
        const q = e.target.value.toLowerCase().trim();
        const optionsContainer = document.getElementById(`options-${cfg.id}`);
        if (optionsContainer) {
          const items = optionsContainer.querySelectorAll(".multi-select-option");
          items.forEach(item => {
            const val = (item.getAttribute("data-value") || item.textContent).toLowerCase();
            if (!q || val.includes(q)) {
              item.style.display = "flex";
            } else {
              item.style.display = "none";
            }
          });
        }
      });
    }

    if (selectAllBtn) {
      selectAllBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const optionsContainer = document.getElementById(`options-${cfg.id}`);
        if (optionsContainer) {
          const visibleOptions = optionsContainer.querySelectorAll('.multi-select-option:not([style*="display: none"])');
          visibleOptions.forEach(opt => {
            const cb = opt.querySelector('input[type="checkbox"]');
            if (cb) {
              cb.checked = true;
              cfg.set.add(cb.value);
              opt.classList.add("is-selected");
            }
          });
        }
        updateDropdownTrigger(cfg);
        applyFilters();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        cfg.set.clear();
        const optionsContainer = document.getElementById(`options-${cfg.id}`);
        if (optionsContainer) {
          optionsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.checked = false;
            cb.closest(".multi-select-option")?.classList.remove("is-selected");
          });
        }
        updateDropdownTrigger(cfg);
        applyFilters();
      });
    }
  });

  // Close menus on outside click
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".multi-select-dropdown")) {
      document.querySelectorAll(".multi-select-dropdown").forEach(d => d.classList.remove("open"));
    }
  });
}

function updateDropdownTrigger(cfg) {
  const label = document.getElementById(`label-${cfg.id}`);
  const badge = document.getElementById(`badge-${cfg.id}`);
  const status = document.getElementById(`status-${cfg.id}`);

  const count = cfg.set.size;
  if (status) {
    status.textContent = `${count} selected`;
  }

  if (count === 0) {
    if (label) label.textContent = cfg.defaultLabel;
    if (badge) badge.style.display = "none";
  } else if (count === 1) {
    const firstVal = Array.from(cfg.set)[0];
    let displayVal = firstVal;
    if (firstVal === "__remote__") displayVal = "Remote Only";
    else if (firstVal === "__onsite__") displayVal = "On-site / Hybrid";
    else if (cfg.id === "interview") {
      const stageNames = {
        no_call: "No Call Yet",
        screening: "Screening",
        technical: "Tech Round",
        final_round: "Final Round",
        offered: "Offer Received",
        rejected: "Rejected"
      };
      displayVal = stageNames[firstVal] || firstVal;
    } else if (cfg.id === "date") {
      const dateNames = {
        "0sec": "0-Sec / Last Hour",
        "24h": "Today (Last 24h)",
        "7d": "Past 7 Days",
        "30d": "Past 30 Days"
      };
      displayVal = dateNames[firstVal] || firstVal;
    }
    if (label) label.textContent = displayVal;
    if (badge) {
      badge.textContent = "1";
      badge.style.display = "inline-block";
    }
  } else {
    if (label) label.textContent = `${count} ${cfg.unit}`;
    if (badge) {
      badge.textContent = count;
      badge.style.display = "inline-block";
    }
  }
}

window.handleOptionToggle = function(type, checkbox) {
  const cfg = getDropdownConfig(type);
  if (!cfg) return;

  if (checkbox.checked) {
    cfg.set.add(checkbox.value);
    checkbox.closest(".multi-select-option")?.classList.add("is-selected");
  } else {
    cfg.set.delete(checkbox.value);
    checkbox.closest(".multi-select-option")?.classList.remove("is-selected");
  }

  updateDropdownTrigger(cfg);
  applyFilters();
};

function resetAllFilters() {
  const searchInput = document.getElementById("job-search-input");
  if (searchInput) searchInput.value = "";

  const filterChips = document.querySelectorAll(".filter-chip");
  filterChips.forEach(c => c.classList.remove("active"));
  const allChip = document.querySelector('.filter-chip[data-filter="all"]');
  if (allChip) allChip.classList.add("active");
  activeFilter = "all";

  selectedCompanies.clear();
  selectedLocations.clear();
  selectedDates.clear();
  selectedInterviewStages.clear();

  // Reset checkboxes and states in dropdowns
  document.querySelectorAll(".multi-select-options input[type='checkbox']").forEach(cb => {
    cb.checked = false;
    cb.closest(".multi-select-option")?.classList.remove("is-selected");
  });

  document.querySelectorAll(".multi-select-search-input").forEach(input => {
    input.value = "";
  });

  document.querySelectorAll(".multi-select-option").forEach(opt => {
    opt.style.display = "flex";
  });

  ["company", "location", "date", "interview"].forEach(type => {
    updateDropdownTrigger(getDropdownConfig(type));
  });

  applyFilters();
}

function updateStatPills(jobs) {
  const totalPill = document.getElementById("total-jobs-pill");
  const appliedPill = document.getElementById("applied-jobs-pill");
  const interviewPill = document.getElementById("interview-jobs-pill");

  const total = allJobs.length;
  const filtered = filteredJobs ? filteredJobs.length : total;
  const applied = allJobs.filter(j => j.applied_status && j.applied_status !== "not_applied").length;
  const interview = allJobs.filter(j => j.interview_status && j.interview_status !== "no_call").length;

  if (totalPill) {
    if (filtered < total) {
      totalPill.textContent = `${filtered} / ${total} JOBS`;
      totalPill.title = `${filtered} positions matching filters out of ${total} total verified positions in database`;
    } else {
      totalPill.textContent = `${total} TOTAL JOBS`;
      totalPill.title = `${total} total verified positions in database`;
    }
  }
  if (appliedPill) {
    appliedPill.textContent = `${applied} APPLIED`;
  }
  if (interviewPill) {
    interviewPill.textContent = `${interview} INTERVIEWS`;
  }
}

async function loadJobs() {
  const tbody = document.getElementById("jobs-table-body");
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-table-cell">Scanning neural database for verified jobs...</td></tr>`;
  }

  try {
    const res = await fetch("/api/jobs?limit=500");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allJobs = await res.json();
    populateFilterDropdowns(allJobs);
    updateStatPills(allJobs);
    applyFilters();
  } catch (err) {
    console.error("Failed to load jobs:", err);
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-table-cell" style="color: var(--neon-magenta);">Failed to connect to jobs database: ${err.message}</td></tr>`;
    }
  }
}

function populateFilterDropdowns(jobs) {
  // 1. Companies Multi-Select
  const companyOptionsContainer = document.getElementById("options-company");
  if (companyOptionsContainer) {
    const compCounts = new Map();
    jobs.forEach(j => {
      const c = (j.fields?.company || j.domain || "Direct Employer").trim();
      if (c) {
        compCounts.set(c, (compCounts.get(c) || 0) + 1);
      }
    });

    const sortedCompanies = Array.from(compCounts.keys()).sort((a, b) => a.localeCompare(b));
    let compHtml = "";
    if (sortedCompanies.length === 0) {
      compHtml = `<div style="padding: 12px; color: var(--text-muted); font-size: 11px; text-align: center; font-family: 'Share Tech Mono', monospace;">No companies detected</div>`;
    } else {
      sortedCompanies.forEach(c => {
        const isSelected = selectedCompanies.has(c);
        compHtml += `
          <label class="multi-select-option ${isSelected ? 'is-selected' : ''}" data-value="${escapeHtml(c)}">
            <input type="checkbox" class="cyber-checkbox" value="${escapeHtml(c)}" ${isSelected ? 'checked' : ''} onchange="handleOptionToggle('company', this)" />
            <span class="ms-opt-text">${escapeHtml(c)}</span>
            <span class="ms-opt-count">${compCounts.get(c)}</span>
          </label>
        `;
      });
    }
    companyOptionsContainer.innerHTML = compHtml;
    updateDropdownTrigger(getDropdownConfig("company"));
  }

  // 2. Locations Multi-Select
  const locationOptionsContainer = document.getElementById("options-location");
  if (locationOptionsContainer) {
    let remoteCount = 0;
    let onsiteCount = 0;
    const locCounts = new Map();

    jobs.forEach(j => {
      const rawLoc = j.fields?.location || "";
      const cleanLoc = formatLocationDisplay(rawLoc);
      const isRemote = rawLoc.toLowerCase().includes("remote") || cleanLoc.toLowerCase().includes("remote");

      if (isRemote) remoteCount++;
      else onsiteCount++;

      if (cleanLoc && !cleanLoc.toLowerCase().includes("unspecified")) {
        locCounts.set(cleanLoc, (locCounts.get(cleanLoc) || 0) + 1);
      }
    });

    const sortedLocs = Array.from(locCounts.keys()).sort((a, b) => a.localeCompare(b));

    const isRemoteSelected = selectedLocations.has("__remote__");
    const isOnsiteSelected = selectedLocations.has("__onsite__");

    let locHtml = `
      <label class="multi-select-option ${isRemoteSelected ? 'is-selected' : ''}" data-value="__remote__">
        <input type="checkbox" class="cyber-checkbox" value="__remote__" ${isRemoteSelected ? 'checked' : ''} onchange="handleOptionToggle('location', this)" />
        <span class="ms-opt-text">🌐 Remote Only</span>
        <span class="ms-opt-count">${remoteCount}</span>
      </label>
      <label class="multi-select-option ${isOnsiteSelected ? 'is-selected' : ''}" data-value="__onsite__">
        <input type="checkbox" class="cyber-checkbox" value="__onsite__" ${isOnsiteSelected ? 'checked' : ''} onchange="handleOptionToggle('location', this)" />
        <span class="ms-opt-text">🏢 On-site / Hybrid</span>
        <span class="ms-opt-count">${onsiteCount}</span>
      </label>
    `;

    sortedLocs.forEach(loc => {
      if (loc.toLowerCase() !== "remote") {
        const isSelected = selectedLocations.has(loc);
        locHtml += `
          <label class="multi-select-option ${isSelected ? 'is-selected' : ''}" data-value="${escapeHtml(loc)}">
            <input type="checkbox" class="cyber-checkbox" value="${escapeHtml(loc)}" ${isSelected ? 'checked' : ''} onchange="handleOptionToggle('location', this)" />
            <span class="ms-opt-text">📍 ${escapeHtml(loc)}</span>
            <span class="ms-opt-count">${locCounts.get(loc)}</span>
          </label>
        `;
      }
    });
    locationOptionsContainer.innerHTML = locHtml;
    updateDropdownTrigger(getDropdownConfig("location"));
  }

  // 3. Date Scraped Multi-Select
  const dateOptionsContainer = document.getElementById("options-date");
  if (dateOptionsContainer) {
    const dateDefs = [
      { key: "0sec", label: "⚡ 0-Sec / Last Hour" },
      { key: "24h", label: "📅 Today (Last 24h)" },
      { key: "7d", label: "🗓️ Past 7 Days" },
      { key: "30d", label: "📆 Past 30 Days" }
    ];
    let dateHtml = "";
    dateDefs.forEach(d => {
      const isSelected = selectedDates.has(d.key);
      dateHtml += `
        <label class="multi-select-option ${isSelected ? 'is-selected' : ''}" data-value="${d.key}">
          <input type="checkbox" class="cyber-checkbox" value="${d.key}" ${isSelected ? 'checked' : ''} onchange="handleOptionToggle('date', this)" />
          <span class="ms-opt-text">${d.label}</span>
        </label>
      `;
    });
    dateOptionsContainer.innerHTML = dateHtml;
    updateDropdownTrigger(getDropdownConfig("date"));
  }

  // 4. Interview Call Stage Multi-Select
  const interviewOptionsContainer = document.getElementById("options-interview");
  if (interviewOptionsContainer) {
    const stageDefs = [
      { key: "no_call", label: "⚪ No Call Yet" },
      { key: "screening", label: "📞 Screening" },
      { key: "technical", label: "💻 Tech Round" },
      { key: "final_round", label: "🎯 Final Round" },
      { key: "offered", label: "🏆 Offer Received" },
      { key: "rejected", label: "🚫 Rejected" }
    ];

    const stageCounts = new Map();
    jobs.forEach(j => {
      const st = j.interview_status || "no_call";
      stageCounts.set(st, (stageCounts.get(st) || 0) + 1);
    });

    let intHtml = "";
    stageDefs.forEach(s => {
      const isSelected = selectedInterviewStages.has(s.key);
      intHtml += `
        <label class="multi-select-option ${isSelected ? 'is-selected' : ''}" data-value="${s.key}">
          <input type="checkbox" class="cyber-checkbox" value="${s.key}" ${isSelected ? 'checked' : ''} onchange="handleOptionToggle('interview', this)" />
          <span class="ms-opt-text">${s.label}</span>
          <span class="ms-opt-count">${stageCounts.get(s.key) || 0}</span>
        </label>
      `;
    });
    interviewOptionsContainer.innerHTML = intHtml;
    updateDropdownTrigger(getDropdownConfig("interview"));
  }
}

function applyFilters() {
  const searchInput = document.getElementById("job-search-input");
  const query = (searchInput ? searchInput.value : "").toLowerCase().trim();
  const nowMs = Date.now();

  filteredJobs = allJobs.filter(job => {
    const f = job.fields || {};
    const title = (f.title || f.name || "").toLowerCase();
    const company = (f.company || job.domain || "").toLowerCase();
    const cleanLocation = formatLocationDisplay(f.location);
    const rawLocation = (f.location || "").toLowerCase();
    const domain = (job.domain || "").toLowerCase();
    const notes = (job.notes || "").toLowerCase();

    // 1. Text search
    const matchesQuery = !query || 
      title.includes(query) || 
      company.includes(query) || 
      cleanLocation.toLowerCase().includes(query) || 
      rawLocation.includes(query) ||
      domain.includes(query) ||
      notes.includes(query);
    if (!matchesQuery) return false;

    // 2. Status chips filter
    if (activeFilter === "applied" && (!job.applied_status || job.applied_status === "not_applied")) return false;
    if (activeFilter === "interview" && (!job.interview_status || job.interview_status === "no_call")) return false;
    if (activeFilter === "not_applied" && job.applied_status && job.applied_status !== "not_applied") return false;
    if (activeFilter === "0sec") {
      const badge = f.freshness_badge || "";
      const age = f.posted_age_seconds;
      const is0sec = badge.includes("0-SEC") || badge.includes("JUST NOW") || (age !== undefined && age < 3600);
      if (!is0sec) return false;
    }

    // 3. Multi-Select Company Filter
    if (selectedCompanies.size > 0) {
      const jobComp = (f.company || job.domain || "").trim();
      if (!selectedCompanies.has(jobComp)) return false;
    }

    // 4. Multi-Select Location Filter
    if (selectedLocations.size > 0) {
      const isRemote = rawLocation.includes("remote") || cleanLocation.toLowerCase().includes("remote");
      let locMatched = false;

      for (const selLoc of selectedLocations) {
        if (selLoc === "__remote__" && isRemote) {
          locMatched = true;
          break;
        }
        if (selLoc === "__onsite__" && !isRemote) {
          locMatched = true;
          break;
        }
        const selLower = selLoc.toLowerCase();
        if (cleanLocation.toLowerCase().includes(selLower) || rawLocation.includes(selLower)) {
          locMatched = true;
          break;
        }
      }
      if (!locMatched) return false;
    }

    // 5. Multi-Select Date / Scraped Age Filter
    if (selectedDates.size > 0) {
      const scrapedTime = job.scraped_at ? new Date(job.scraped_at).getTime() : 0;
      const ageSec = f.posted_age_seconds;
      const diffSec = scrapedTime ? (nowMs - scrapedTime) / 1000 : Infinity;
      const effectiveSec = Math.min(diffSec, ageSec !== undefined ? ageSec : Infinity);
      const badge = f.freshness_badge || "";
      const is0sec = badge.includes("0-SEC") || badge.includes("JUST NOW") || effectiveSec < 3600;

      let dateMatched = false;
      for (const selDate of selectedDates) {
        if (selDate === "0sec" && is0sec) {
          dateMatched = true;
          break;
        }
        if (selDate === "24h" && effectiveSec <= 86400) {
          dateMatched = true;
          break;
        }
        if (selDate === "7d" && effectiveSec <= 7 * 86400) {
          dateMatched = true;
          break;
        }
        if (selDate === "30d" && effectiveSec <= 30 * 86400) {
          dateMatched = true;
          break;
        }
      }
      if (!dateMatched) return false;
    }

    // 6. Multi-Select Interview Call Stage Filter
    if (selectedInterviewStages.size > 0) {
      const currentStage = job.interview_status || "no_call";
      if (!selectedInterviewStages.has(currentStage)) return false;
    }

    return true;
  });

  // Update counter badge
  const countBadge = document.getElementById("filtered-count-badge");
  if (countBadge) {
    countBadge.textContent = `SHOWING ${filteredJobs.length} OF ${allJobs.length}`;
  }

  // Update stat pills at the top
  updateStatPills(allJobs);

  // Update select-all checkbox state
  updateBulkControls();
  renderTable(filteredJobs);
}

function updateBulkControls() {
  const bulkBtn = document.getElementById("bulk-delete-btn");
  const selCountSpan = document.getElementById("selected-count");
  const selectAll = document.getElementById("select-all-jobs");

  const count = selectedJobIds.size;
  if (selCountSpan) selCountSpan.textContent = count;

  if (bulkBtn) {
    bulkBtn.style.display = count > 0 ? "inline-flex" : "none";
  }

  if (selectAll) {
    if (filteredJobs.length > 0 && filteredJobs.every(j => selectedJobIds.has(j.id))) {
      selectAll.checked = true;
      selectAll.indeterminate = false;
    } else if (filteredJobs.some(j => selectedJobIds.has(j.id))) {
      selectAll.checked = false;
      selectAll.indeterminate = true;
    } else {
      selectAll.checked = false;
      selectAll.indeterminate = false;
    }
  }
}

function toggleSelectAll(isChecked) {
  if (isChecked) {
    filteredJobs.forEach(j => selectedJobIds.add(j.id));
  } else {
    filteredJobs.forEach(j => selectedJobIds.delete(j.id));
  }
  updateBulkControls();
  renderTable(filteredJobs);
}

function toggleJobSelection(recordId, isChecked) {
  if (isChecked) {
    selectedJobIds.add(recordId);
  } else {
    selectedJobIds.delete(recordId);
  }
  updateBulkControls();
  const row = document.getElementById(`job-row-${recordId}`);
  if (row) {
    if (isChecked) row.classList.add("selected-row");
    else row.classList.remove("selected-row");
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderTable(jobs) {
  const tbody = document.getElementById("jobs-table-body");
  if (!tbody) return;

  if (!jobs || jobs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-table-cell">No job listings found matching the current criteria.</td></tr>`;
    return;
  }

  let html = "";
  jobs.forEach((job, idx) => {
    const f = job.fields || {};
    const title = f.title || f.name || "Open Position";
    const company = f.company || job.domain || "Direct Employer";
    const location = formatLocationDisplay(f.location);
    const salary = f.salary || "Competitive";
    const badge = f.freshness_badge || "Recent";
    const is0sec = badge.includes("0-SEC") || badge.includes("JUST NOW");
    const applyUrl = f.application_url || job.canonical_url || "#";
    const scrapedDate = formatDate(job.scraped_at || f.posted_at);

    const appliedStatus = job.applied_status || "not_applied";
    const interviewStatus = job.interview_status || "no_call";
    const isSelected = selectedJobIds.has(job.id);

    html += `
      <tr class="job-row ${isSelected ? 'selected-row' : ''}" id="job-row-${job.id}" onclick="openJobDetail('${job.id}')">
        <td style="text-align: center;" onclick="event.stopPropagation();">
          <input type="checkbox" class="cyber-checkbox job-select-checkbox" data-id="${job.id}" ${isSelected ? 'checked' : ''} onchange="toggleJobSelection('${job.id}', this.checked)" />
        </td>
        <td style="font-family: 'Share Tech Mono', monospace; color: var(--text-muted);">${idx + 1}</td>
        <td>
          <div class="table-job-title">
            <span>${escapeHtml(title)}</span>
            ${is0sec ? `<span class="zero-sec-badge" style="font-size: 9px; padding: 1px 6px;">0-SEC</span>` : ''}
          </div>
          <div class="table-company">@ ${escapeHtml(company)}</div>
        </td>
        <td>
          <div style="color: #c9ddf0;">📍 ${escapeHtml(location)}</div>
          <div class="salary-tag" style="margin-top: 2px;">💰 ${escapeHtml(salary)}</div>
        </td>
        <td>
          <div style="font-family: 'Share Tech Mono', monospace; font-size: 11px; color: #fff;">
            ${scrapedDate}
          </div>
          <div class="table-meta-sub">${escapeHtml(badge)}</div>
        </td>
        <td onclick="event.stopPropagation();">
          <select id="applied-select-${job.id}" class="table-status-select ${appliedStatus}" onchange="updateJobApplied('${job.id}', this.value); this.className = 'table-status-select ' + this.value;">
            <option value="not_applied" ${appliedStatus === 'not_applied' ? 'selected' : ''}>⚪ Not Applied</option>
            <option value="applied" ${appliedStatus === 'applied' ? 'selected' : ''}>🔵 Applied</option>
            <option value="interviewing" ${appliedStatus === 'interviewing' ? 'selected' : ''}>🟡 Interviewing</option>
            <option value="offered" ${appliedStatus === 'offered' ? 'selected' : ''}>🟢 Offered</option>
            <option value="rejected" ${appliedStatus === 'rejected' ? 'selected' : ''}>🔴 Rejected</option>
          </select>
        </td>
        <td onclick="event.stopPropagation();">
          <select id="interview-select-${job.id}" class="table-status-select ${interviewStatus !== 'no_call' ? 'interviewing' : ''}" onchange="updateJobInterview('${job.id}', this.value); this.className = 'table-status-select ' + (this.value !== 'no_call' ? 'interviewing' : '');">
            <option value="no_call" ${interviewStatus === 'no_call' ? 'selected' : ''}>⚪ No Call</option>
            <option value="screening" ${interviewStatus === 'screening' ? 'selected' : ''}>📞 Screening</option>
            <option value="technical" ${interviewStatus === 'technical' ? 'selected' : ''}>💻 Tech Round</option>
            <option value="final_round" ${interviewStatus === 'final_round' ? 'selected' : ''}>🎯 Final Round</option>
            <option value="offered" ${interviewStatus === 'offered' ? 'selected' : ''}>🏆 Offered</option>
            <option value="rejected" ${interviewStatus === 'rejected' ? 'selected' : ''}>🚫 Rejected</option>
          </select>
        </td>
        <td onclick="event.stopPropagation();">
          <div class="table-action-btns">
            <button class="table-view-btn" onclick="openJobDetail('${job.id}')" title="Inspect Full JD & Timestamp">
              VIEW
            </button>
            <a href="${applyUrl}" target="_blank" rel="noopener noreferrer" class="table-apply-btn" onclick="handleApplyClick('${job.id}', event)" title="Open ATS Application Form">
              APPLY ➔
            </a>
            <button class="table-delete-btn" onclick="deleteJob('${job.id}', event)" title="Delete this job record">
              🗑️
            </button>
          </div>
        </td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

window.deleteJob = async function(recordId, event) {
  if (event) event.stopPropagation();

  const confirmed = confirm("Are you sure you want to permanently delete this job record?");
  if (!confirmed) return;

  try {
    const res = await fetch(`/api/jobs/${recordId}`, {
      method: "DELETE"
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }

    // Remove from local collections
    allJobs = allJobs.filter(j => j.id !== recordId);
    selectedJobIds.delete(recordId);

    // Close modal if open for this job
    const modal = document.getElementById("job-detail-modal");
    if (modal && activeJobId === recordId) {
      modal.style.display = "none";
      activeJobId = null;
    }

    updateStatPills(allJobs);
    populateFilterDropdowns(allJobs);
    applyFilters();
  } catch (err) {
    console.error("Delete job error:", err);
    alert("Failed to delete job: " + err.message);
  }
};

async function bulkDeleteJobs() {
  const ids = Array.from(selectedJobIds);
  if (ids.length === 0) return;

  const confirmed = confirm(`Are you sure you want to permanently delete ${ids.length} selected job record(s)?`);
  if (!confirmed) return;

  const bulkBtn = document.getElementById("bulk-delete-btn");
  if (bulkBtn) bulkBtn.textContent = "DELETING...";

  try {
    const res = await fetch("/api/jobs/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record_ids: ids })
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }

    // Remove deleted IDs from allJobs
    const deletedSet = new Set(ids);
    allJobs = allJobs.filter(j => !deletedSet.has(j.id));
    selectedJobIds.clear();

    const modal = document.getElementById("job-detail-modal");
    if (modal && deletedSet.has(activeJobId)) {
      modal.style.display = "none";
      activeJobId = null;
    }

    updateStatPills(allJobs);
    populateFilterDropdowns(allJobs);
    applyFilters();
  } catch (err) {
    console.error("Bulk delete error:", err);
    alert("Failed to bulk delete jobs: " + err.message);
  } finally {
    updateBulkControls();
  }
}

function formatDate(iso) {
  if (!iso) return "Just now";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + " " +
           d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch (e) {
    return iso;
  }
}

window.handleApplyClick = async function(recordId, event) {
  if (event) event.stopPropagation();

  // Find job in allJobs
  const job = allJobs.find(j => j.id === recordId);
  if (!job) return;

  // If status is not already "applied", immediately switch to "applied"
  if (job.applied_status !== "applied") {
    // 1. Optimistically update local job object
    job.applied_status = "applied";
    job.applied_at = new Date().toISOString();

    // 2. Immediately update the dropdown in the table row
    const appliedSelect = document.getElementById(`applied-select-${recordId}`);
    if (appliedSelect) {
      appliedSelect.value = "applied";
      appliedSelect.className = "table-status-select applied";
    }

    // 3. If the detail modal is currently open for this job, update its fields too
    if (activeJobId === recordId) {
      const modalSelect = document.getElementById("modal-applied-select");
      if (modalSelect) modalSelect.value = "applied";
      const appliedAtInput = document.getElementById("modal-applied-at-input");
      if (appliedAtInput && !appliedAtInput.value) {
        appliedAtInput.value = new Date().toISOString().slice(0, 16);
      }
    }

    // 4. Update the summary statistics pills
    updateStatPills(allJobs);

    // 5. Persist status to database
    try {
      await updateJobApplied(recordId, "applied");
    } catch (err) {
      console.error("Auto-apply update error:", err);
    }
  }
};

async function updateJobApplied(recordId, newStatus) {
  try {
    const res = await fetch(`/api/jobs/${recordId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applied_status: newStatus })
    });
    if (!res.ok) throw new Error("Update failed");
    const updated = await res.json();
    
    // Update local cache
    const idx = allJobs.findIndex(j => j.id === recordId);
    if (idx !== -1) {
      allJobs[idx].applied_status = updated.applied_status;
      allJobs[idx].applied_at = updated.applied_at;
    }
    updateStatPills(allJobs);

    // Update row select element if present
    const selectEl = document.getElementById(`applied-select-${recordId}`);
    if (selectEl) {
      selectEl.value = updated.applied_status;
      selectEl.className = `table-status-select ${updated.applied_status}`;
    }
  } catch (err) {
    console.error("Failed to update applied status", err);
    alert("Error saving applied status: " + err.message);
  }
}
window.updateJobApplied = updateJobApplied;

async function updateJobInterview(recordId, newStatus) {
  try {
    const res = await fetch(`/api/jobs/${recordId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interview_status: newStatus })
    });
    if (!res.ok) throw new Error("Update failed");
    const updated = await res.json();
    
    // Update local cache
    const idx = allJobs.findIndex(j => j.id === recordId);
    if (idx !== -1) {
      allJobs[idx].interview_status = updated.interview_status;
    }
    updateStatPills(allJobs);

    const selectEl = document.getElementById(`interview-select-${recordId}`);
    if (selectEl) {
      selectEl.value = updated.interview_status;
      selectEl.className = `table-status-select ${updated.interview_status !== 'no_call' ? 'interviewing' : ''}`;
    }
  } catch (err) {
    console.error("Failed to update interview status", err);
    alert("Error saving interview status: " + err.message);
  }
}
window.updateJobInterview = updateJobInterview;

window.openJobDetail = async function(recordId) {
  activeJobId = recordId;
  const modal = document.getElementById("job-detail-modal");
  if (!modal) return;

  modal.style.display = "flex";

  // Prepopulate with summary
  const job = allJobs.find(j => j.id === recordId) || {};
  const f = job.fields || {};

  document.getElementById("modal-job-title").textContent = f.title || f.name || "Role Listing";
  document.getElementById("modal-job-company").textContent = `@ ${f.company || job.domain || 'Direct Employer'}`;
  document.getElementById("modal-job-location").textContent = formatLocationDisplay(f.location);
  document.getElementById("modal-job-salary").textContent = f.salary || "Competitive";
  document.getElementById("modal-job-timestamp").textContent = formatDate(job.scraped_at || f.posted_at);
  document.getElementById("modal-job-freshness").textContent = f.freshness_badge || "Verified";

  const applyLink = document.getElementById("modal-direct-apply-link");
  if (applyLink) {
    applyLink.href = f.application_url || job.canonical_url || "#";
    applyLink.onclick = function(e) {
      handleApplyClick(recordId, e);
    };
  }

  document.getElementById("modal-applied-select").value = job.applied_status || "not_applied";
  document.getElementById("modal-interview-select").value = job.interview_status || "no_call";
  document.getElementById("modal-notes-input").value = job.notes || "";

  const appliedAtInput = document.getElementById("modal-applied-at-input");
  if (appliedAtInput) {
    if (job.applied_at) {
      try {
        appliedAtInput.value = new Date(job.applied_at).toISOString().slice(0, 16);
      } catch(e) {
        appliedAtInput.value = "";
      }
    } else {
      appliedAtInput.value = "";
    }
  }

  const jdBody = document.getElementById("modal-jd-body");
  jdBody.innerHTML = "Fetching complete job description and document text from database...";

  try {
    const res = await fetch(`/api/jobs/${recordId}`);
    if (!res.ok) throw new Error("Could not load details");
    const detail = await res.json();

    // Set complete Job Description
    if (detail.job_description && detail.job_description.trim()) {
      if (typeof marked !== "undefined") {
        const rawHtml = marked.parse(detail.job_description);
        jdBody.innerHTML = typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(rawHtml) : rawHtml;
      } else {
        jdBody.textContent = detail.job_description;
      }
    } else {
      jdBody.innerHTML = `<em>Structured job description extracted directly from ATS schema. Full document text verified.</em>`;
    }

    // Set source
    const sourceEl = document.getElementById("modal-job-source");
    if (sourceEl) {
      sourceEl.textContent = `Source: ${detail.domain || 'Direct Employer ATS'}`;
    }

    // Evidence
    const eviBox = document.getElementById("modal-evidence-box");
    const eviList = document.getElementById("modal-evidence-list");
    if (detail.evidence && detail.evidence.length > 0) {
      eviBox.style.display = "block";
      eviList.innerHTML = detail.evidence.map(e => `
        <div class="evidence-item">
          <strong>${e.field.toUpperCase()}:</strong> "${e.text}"
        </div>
      `).join("");
    } else {
      eviBox.style.display = "none";
    }

  } catch (err) {
    console.error("Failed to fetch full job detail", err);
    jdBody.textContent = "Error loading complete job description: " + err.message;
  }
};

async function saveModalStatus() {
  if (!activeJobId) return;

  const appliedStatus = document.getElementById("modal-applied-select").value;
  const interviewStatus = document.getElementById("modal-interview-select").value;
  const appliedAt = document.getElementById("modal-applied-at-input").value;
  const notes = document.getElementById("modal-notes-input").value;

  const saveBtn = document.getElementById("modal-save-status-btn");
  if (saveBtn) saveBtn.textContent = "SAVING...";

  try {
    const res = await fetch(`/api/jobs/${activeJobId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        applied_status: appliedStatus,
        applied_at: appliedAt ? new Date(appliedAt).toISOString() : null,
        interview_status: interviewStatus,
        notes: notes
      })
    });

    if (!res.ok) throw new Error("Failed to save status");
    const updated = await res.json();

    // Update in local cache
    const idx = allJobs.findIndex(j => j.id === activeJobId);
    if (idx !== -1) {
      allJobs[idx].applied_status = updated.applied_status;
      allJobs[idx].applied_at = updated.applied_at;
      allJobs[idx].interview_status = updated.interview_status;
      allJobs[idx].notes = updated.notes;
    }

    updateStatPills(allJobs);
    applyFilters();

    if (saveBtn) {
      saveBtn.textContent = "SAVED ✓";
      setTimeout(() => { saveBtn.textContent = "SAVE STATUS"; }, 1500);
    }
  } catch (err) {
    console.error("Save error:", err);
    if (saveBtn) saveBtn.textContent = "SAVE STATUS";
    alert("Error saving status: " + err.message);
  }
}