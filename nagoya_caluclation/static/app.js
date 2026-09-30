"use strict";

const setup = document.querySelector("#setup");
const workspace = document.querySelector("#workspace");
const keyField = document.querySelector("#key-field");
const keyHelp = document.querySelector("#key-help");
const apiKeyInput = document.querySelector("#api-key");
const startButton = document.querySelector("#start-button");
const scenarioButton = document.querySelector("#scenario-button");
const scenarioList = document.querySelector("#scenario-list");
const scenarioError = document.querySelector("#scenario-error");
const newButton = document.querySelector("#new-button");
const messages = document.querySelector("#messages");
const form = document.querySelector("#message-form");
const messageInput = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const errorBox = document.querySelector("#error");
const conditionList = document.querySelector("#condition-list");
const researchPanel = document.querySelector("#research-panel");
const researchSummary = document.querySelector("#research-summary");
const researchSources = document.querySelector("#research-sources");
const resultsPanel = document.querySelector("#results");
const totalValue = document.querySelector("#total-value");
const adjustedBlock = document.querySelector("#adjusted-block");
const adjustedValue = document.querySelector("#adjusted-value");
const resultItems = document.querySelector("#result-items");
const csvLink = document.querySelector("#csv-link");
const agePanel = document.querySelector("#age-panel");
const ageTotal = document.querySelector("#age-total");
const ageLegend = document.querySelector("#age-legend");
const ageChart = document.querySelector("#age-chart");
const ageNote = document.querySelector("#age-note");

let sessionId = null;
let apiKeyConfigured = false;

const labels = {
  commercial: "商業施設",
  office: "オフィス",
  hotel: "ホテル",
};

const genderColors = {
  male: "#2f6ea5",
  female: "#d7892e",
  other: "#84908c",
};

async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || `通信に失敗しました（${response.status}）`);
  }
  return payload;
}

function formatNumber(value, maximumFractionDigits = 0) {
  return new Intl.NumberFormat("ja-JP", { maximumFractionDigits }).format(value);
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.toggle("hidden", !message);
}

function addMessage(role, text, pending = false) {
  const bubble = document.createElement("div");
  bubble.className = `message ${role}${pending ? " pending" : ""}`;
  const roleLabel = document.createElement("span");
  roleLabel.className = "message-role";
  roleLabel.textContent = role === "assistant" ? "アシスタント" : "あなた";
  const body = document.createElement("p");
  body.textContent = text;
  bubble.append(roleLabel, body);
  messages.append(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function addCondition(label, value) {
  const row = document.createElement("div");
  row.className = "condition-row";
  const term = document.createElement("span");
  term.textContent = label;
  const detail = document.createElement("b");
  detail.textContent = value;
  row.append(term, detail);
  conditionList.append(row);
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_error) {
    return null;
  }
}

function renderResearch(research) {
  const sources = Array.isArray(research?.sources) ? research.sources : [];
  const summary = typeof research?.summary === "string" ? research.summary : "";
  researchPanel.classList.toggle("hidden", !summary && !sources.length);
  researchSummary.textContent = summary;
  researchSources.replaceChildren();
  sources.forEach((source) => {
    const href = safeExternalUrl(source.url);
    if (!href) return;
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = source.title || new URL(href).hostname;
    item.append(link);
    researchSources.append(item);
  });
}

function renderState(state) {
  conditionList.replaceChildren();
  const facilities = state.facility_types || [];
  renderResearch(state.web_research);
  if (state.target_facility_name) {
    addCondition("対象施設", state.target_facility_name);
  }
  if (!facilities.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.target_facility_name
      ? "用途は調査・確認中です。"
      : "会話から確認できた条件がここに表示されます。";
    conditionList.append(empty);
    return;
  }
  addCondition("用途", facilities.map((item) => labels[item]).join("・"));
  if (state.station_distance_m !== null) {
    addCondition("駅距離", `${formatNumber(state.station_distance_m, 1)} m`);
  }
  if (facilities.includes("commercial")) {
    const item = state.commercial;
    if (item.area_m2 !== null) addCondition("商業面積", `${formatNumber(item.area_m2)} ㎡`);
    if (item.area_type) addCondition("商業地域区分", item.area_type.toUpperCase());
    if (item.day) addCondition("対象日", item.day);
  }
  if (facilities.includes("office")) {
    const item = state.office;
    if (item.area_m2 !== null) addCondition("オフィス面積", `${formatNumber(item.area_m2)} ㎡`);
    if (item.location_key) addCondition("オフィス地域", item.location_key === "a" ? "都心部" : "周辺部");
    if (item.building_key) addCondition("ビル種別", item.building_key === "a" ? "一般事務所ビル" : "単館型事務所ビル");
    if (item.commercial_floor_ratio !== null) addCondition("商業床面積率", `${formatNumber(item.commercial_floor_ratio, 1)} %`);
  }
  if (facilities.includes("hotel") && state.hotel.area_m2 !== null) {
    addCondition("ホテル面積", `${formatNumber(state.hotel.area_m2)} ㎡`);
  }
  if (state.apply_internal_trip_reduction !== null) {
    addCondition("内々交通5%低減", state.apply_internal_trip_reduction ? "表示する" : "表示しない");
  }
}

function renderResults(result) {
  resultsPanel.classList.remove("hidden");
  totalValue.textContent = formatNumber(result.total);
  if (result.adjusted_total !== null) {
    adjustedBlock.classList.remove("hidden");
    adjustedValue.textContent = formatNumber(result.adjusted_total);
  } else {
    adjustedBlock.classList.add("hidden");
  }
  resultItems.replaceChildren();
  result.items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "result-item";
    const heading = document.createElement("div");
    const name = document.createElement("b");
    name.textContent = item.name;
    const category = document.createElement("span");
    category.textContent = item.category;
    heading.append(name, category);
    const value = document.createElement("strong");
    value.textContent = `${formatNumber(item.trip_ends)} 人TE/日`;
    const detail = document.createElement("small");
    detail.textContent = `適用原単位 ${formatNumber(item.applied_rate)} 人TE/ha・日`;
    card.append(heading, value, detail);
    resultItems.append(card);
  });
  csvLink.href = result.csv_url;
}

function populateAgeLegend(container, distribution) {
  container.replaceChildren();
  distribution.genders.forEach((gender) => {
    const item = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = genderColors[gender.key];
    item.append(
      swatch,
      document.createTextNode(`${gender.name} ${formatNumber(gender.count)}`),
    );
    container.append(item);
  });
}

function populateAgeChart(container, distribution) {
  container.replaceChildren();
  const maxCount = Math.max(
    1,
    ...distribution.combined_groups.map((group) => group.count),
  );
  distribution.combined_groups.forEach((combined) => {
    const column = document.createElement("div");
    column.className = "age-column";
    const value = document.createElement("strong");
    value.textContent = formatNumber(combined.count);
    const frame = document.createElement("div");
    frame.className = "age-bar-frame";
    const stack = document.createElement("div");
    stack.className = "age-bar-stack";
    stack.style.height = `${(combined.count / maxCount) * 100}%`;

    combined.genders.forEach((gender) => {
      if (gender.count === 0) return;
      const segment = document.createElement("span");
      segment.className = "age-segment";
      segment.style.flexGrow = String(gender.count);
      segment.style.backgroundColor = genderColors[gender.key];
      segment.title = `${combined.age}・${gender.name}: ${formatNumber(gender.count)} 到着人トリップ/日（全体比 ${(gender.share * 100).toFixed(1)}%）`;
      stack.append(segment);
    });
    frame.append(stack);
    const label = document.createElement("span");
    label.className = "age-label";
    label.textContent = combined.age;
    column.append(value, frame, label);
    container.append(column);
  });
}

function renderAgeDistribution(distribution) {
  agePanel.classList.remove("hidden");
  ageTotal.textContent = formatNumber(distribution.estimated_arrivals_total);
  populateAgeLegend(ageLegend, distribution);
  populateAgeChart(ageChart, distribution);
  ageNote.textContent = `${distribution.note} 参照JSON：${distribution.reference_file}。出典：${distribution.source}`;
}

function addScenarioMetric(container, label, value) {
  const row = document.createElement("div");
  const term = document.createElement("span");
  term.textContent = label;
  const number = document.createElement("b");
  number.textContent = value;
  row.append(term, number);
  container.append(row);
}

function renderScenarioDistributions(payload) {
  scenarioList.replaceChildren();
  payload.scenarios.forEach((scenario) => {
    const card = document.createElement("article");
    card.className = "scenario-card";
    const heading = document.createElement("div");
    heading.className = "scenario-card-heading";
    const titleWrap = document.createElement("div");
    const number = document.createElement("span");
    number.textContent = `SCENARIO ${scenario.number}`;
    const title = document.createElement("h3");
    title.textContent = scenario.name;
    const aim = document.createElement("p");
    aim.textContent = scenario.aim;
    const referenceFacility = document.createElement("p");
    referenceFacility.textContent = scenario.reference_facility
      ? `参考施設：${scenario.reference_facility}`
      : "";
    referenceFacility.hidden = !scenario.reference_facility;
    const allocationBasis = document.createElement("p");
    allocationBasis.textContent = `配分根拠：${scenario.allocation_basis}`;
    titleWrap.append(number, title, aim, referenceFacility, allocationBasis);
    const arrival = document.createElement("strong");
    arrival.textContent = formatNumber(
      scenario.age_gender_distribution.estimated_arrivals_total,
    );
    const arrivalUnit = document.createElement("small");
    arrivalUnit.textContent = "推定到着人トリップ/日";
    const arrivalWrap = document.createElement("div");
    arrivalWrap.className = "scenario-arrival";
    arrivalWrap.append(arrival, arrivalUnit);
    heading.append(titleWrap, arrivalWrap);

    const metrics = document.createElement("div");
    metrics.className = "scenario-metrics";
    scenario.facility_results.forEach((facility) => {
      addScenarioMetric(
        metrics,
        `${facility.name} ${formatNumber(facility.area_m2)}㎡`,
        `${formatNumber(facility.trip_ends)} 人TE`,
      );
    });
    addScenarioMetric(
      metrics,
      "単純合計",
      `${formatNumber(scenario.total_trip_ends)} 人TE/日`,
    );
    if (scenario.adjusted_total_trip_ends !== null) {
      addScenarioMetric(
        metrics,
        scenario.internal_trip_reduction > 0
          ? "内々交通5%低減後"
          : "内々交通調整後（低減なし）",
        `${formatNumber(scenario.adjusted_total_trip_ends)} 人TE/日`,
      );
    }

    const legend = document.createElement("div");
    legend.className = "age-legend scenario-legend";
    populateAgeLegend(legend, scenario.age_gender_distribution);
    const scroll = document.createElement("div");
    scroll.className = "age-chart-scroll scenario-chart-scroll";
    scroll.tabIndex = 0;
    const chart = document.createElement("div");
    chart.className = "age-chart scenario-age-chart";
    populateAgeChart(chart, scenario.age_gender_distribution);
    scroll.append(chart);
    card.append(heading, metrics, legend, scroll);
    scenarioList.append(card);
  });
  const note = document.createElement("p");
  note.className = "scenario-note";
  const chartPaths = Object.values(payload.chart_images || {});
  const chartNote = chartPaths.length
    ? ` 英語の研究資料用グラフ：${chartPaths.join(" / ")}`
    : "";
  note.textContent = `${payload.calculation_conditions.note} ${payload.note}${chartNote}`;
  scenarioList.append(note);
  scenarioList.classList.remove("hidden");
}

async function loadScenarioDistributions() {
  scenarioError.classList.add("hidden");
  scenarioButton.disabled = true;
  scenarioButton.textContent = "計算中…";
  try {
    const payload = await requestJSON("/api/scenarios");
    renderScenarioDistributions(payload);
    scenarioButton.textContent = "6案を再計算";
  } catch (error) {
    scenarioError.textContent = error.message;
    scenarioError.classList.remove("hidden");
    scenarioButton.textContent = "再試行";
  } finally {
    scenarioButton.disabled = false;
  }
}

function resetUI() {
  sessionId = null;
  messages.replaceChildren();
  resultsPanel.classList.add("hidden");
  resultItems.replaceChildren();
  adjustedBlock.classList.add("hidden");
  agePanel.classList.add("hidden");
  ageLegend.replaceChildren();
  ageChart.replaceChildren();
  ageNote.textContent = "";
  researchPanel.classList.add("hidden");
  researchSummary.textContent = "";
  researchSources.replaceChildren();
  form.classList.remove("disabled");
  messageInput.disabled = false;
  sendButton.disabled = false;
  messageInput.value = "";
  showError("");
  renderState({ facility_types: [] });
}

async function startSession() {
  showError("");
  startButton.disabled = true;
  startButton.textContent = "準備中…";
  try {
    const apiKey = apiKeyConfigured ? "" : apiKeyInput.value.trim();
    if (!apiKeyConfigured && !apiKey) {
      throw new Error("Gemini APIキーを入力してください。");
    }
    const session = await requestJSON("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    });
    apiKeyInput.value = "";
    resetUI();
    sessionId = session.id;
    setup.classList.add("hidden");
    workspace.classList.remove("hidden");
    addMessage("assistant", session.reply);
    renderState(session.state);
    messageInput.focus();
  } catch (error) {
    keyHelp.textContent = error.message;
    keyHelp.classList.add("setup-error");
  } finally {
    startButton.disabled = false;
    startButton.textContent = "計算を開始";
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text || !sessionId) return;
  showError("");
  addMessage("user", text);
  messageInput.value = "";
  messageInput.disabled = true;
  sendButton.disabled = true;
  const pending = addMessage("assistant", "回答を確認し、必要な情報をWebで調べています…", true);
  try {
    const response = await requestJSON(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    pending.remove();
    addMessage("assistant", response.reply);
    renderState(response.state);
    if (response.status === "complete") {
      renderResults(response.result);
      renderAgeDistribution(response.result.age_distribution);
      form.classList.add("disabled");
    } else if (response.status === "cancelled") {
      form.classList.add("disabled");
    } else {
      messageInput.disabled = false;
      sendButton.disabled = false;
      messageInput.focus();
    }
  } catch (error) {
    pending.remove();
    showError(error.message);
    messageInput.disabled = false;
    sendButton.disabled = false;
    messageInput.value = text;
    messageInput.focus();
  }
}

async function initialize() {
  try {
    const config = await requestJSON("/api/config");
    apiKeyConfigured = config.api_key_configured;
    if (apiKeyConfigured) {
      keyField.classList.add("hidden");
      const searchLabel = config.web_search_enabled ? "・Google検索対応" : "";
      keyHelp.textContent = `サーバーに設定されたGemini APIを使用します（${config.model}${searchLabel}）。`;
    }
  } catch (error) {
    keyHelp.textContent = error.message;
    keyHelp.classList.add("setup-error");
  }
}

startButton.addEventListener("click", startSession);
scenarioButton.addEventListener("click", loadScenarioDistributions);
newButton.addEventListener("click", () => {
  resetUI();
  workspace.classList.add("hidden");
  setup.classList.remove("hidden");
  keyHelp.classList.remove("setup-error");
  keyHelp.textContent = apiKeyConfigured
    ? "サーバーに設定されたGemini APIとGoogle検索を使用します。"
    : "Gemini APIキーは、このアプリのメモリ内だけで使用し保存しません。";
});
form.addEventListener("submit", sendMessage);
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

initialize();
