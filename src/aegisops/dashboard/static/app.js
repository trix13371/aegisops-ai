"use strict";

const paths = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  history:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/><path d="M12 7v5l3 2"/>',
  server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M16 6.5h2M16 17.5h2"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  layers:'<path d="m12 3 9 5-9 5-9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  scan:'<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3M3 12h5l2-4 4 8 2-4h5"/>',
  refresh:'<path d="M20 7a8 8 0 0 0-13-2L3 9m0-6v6h6m-5 8a8 8 0 0 0 13 2l4-4m0 6v-6h-6"/>',
  play:'<path d="m8 4 12 8-12 8Z"/>',
  activity:'<path d="M2 12h5l3-9 4 18 3-9h5"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  close:'<path d="m6 6 12 12M18 6 6 18"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  box:'<path d="m12 3 9 5v8l-9 5-9-5V8Zm0 10v8M3 8l9 5 9-5M7.5 5.5l9 5"/>',
  spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
  database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
};
const $ = id => document.getElementById(id);
function icon(name) {
  const element = document.createElement("span");
  // Only local constant SVG paths enter this HTML sink. API strings use textContent.
  element.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.box}</svg>`;
  return element;
}
document.querySelectorAll("[data-icon]").forEach(element => element.replaceChildren(icon(element.dataset.icon)));
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
const state = {records:[], projects:[], filter:"all", query:"", view:"overview", connected:false, job:null, polling:null, refresh:false, detailOrigin:null};
const statuses = {completed:"Проверено", blocked:"Заблокировано", failed:"Ошибка", passed:"Форматы проверены", issues:"Есть замечания", partial:"Частичная проверка"};
const timeFormat = new Intl.DateTimeFormat("ru-RU", {hour:"2-digit", minute:"2-digit"});
const fullTime = new Intl.DateTimeFormat("ru-RU", {day:"numeric", month:"long", hour:"2-digit", minute:"2-digit"});
const dateFormat = new Intl.DateTimeFormat("ru-RU", {day:"2-digit", month:"2-digit"});
function formatTime(value, full=false) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : (full ? fullTime : timeFormat).format(date);
}
function statusBadge(status) {return node("span", `status ${Object.hasOwn(statuses,status) ? status : "failed"}`, statuses[status] || "Неизвестно");}
function sourceLabel(origin) {return origin === "local_kind" ? "Кластер" : "Учебный";}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {$("toast").hidden = true;}, 3500);
}
function error(message) {$("error-banner").textContent = message || ""; $("error-banner").hidden = !message;}
async function api(path, options={}) {
  const response = await fetch(path, {...options, credentials:"same-origin", signal:AbortSignal.timeout(25000)});
  const body = await response.json();
  if (!response.ok && response.status !== 409) throw new Error(body.error || "Не удалось получить данные. Проверьте локальные сервисы.");
  return body;
}
function renderRecords() {
  const filtered = state.records.filter(record => (state.filter === "all" || state.filter === record.origin) &&
    `${record.title} ${record.signal} ${record.id}`.toLocaleLowerCase("ru").includes(state.query));
  $("records").replaceChildren();
  for (const record of filtered) {
    const row = node("tr", "record-row");
    const objectCell = node("td");
    const object = node("div", "object-cell");
    const mark = node("span", "object-mark"); mark.append(icon(record.incident ? "box" : "check"));
    const description = node("div");
    description.append(node("strong", "", record.title), node("small", "", record.signal));
    object.append(mark, description); objectCell.append(object);
    const result = node("td"); result.append(statusBadge(record.status));
    const source = node("td"); const sourceText = node("span", "source-label");
    sourceText.append(icon(record.origin === "local_kind" ? "server" : "layers"), document.createTextNode(sourceLabel(record.origin)));
    source.append(sourceText);
    const timestamp = node("td"); const time = node("time", "", formatTime(record.time));
    time.dateTime = record.time; time.title = formatTime(record.time, true);
    const date = new Date(record.time);
    if (!Number.isNaN(date.getTime()) && date.toDateString() !== new Date().toDateString()) {
      time.textContent = `${dateFormat.format(date)} · ${formatTime(record.time)}`;
    }
    timestamp.append(time);
    const action = node("td"); const open = node("button", "row-open");
    open.setAttribute("aria-label", `Открыть результат: ${record.title}`); open.append(icon("chevron")); action.append(open);
    row.append(objectCell, result, source, timestamp, action);
    row.addEventListener("click", () => openDetail(record.id, open));
    $("records").append(row);
  }
  $("empty-state").hidden = filtered.length !== 0;
  $("history-total").textContent = filtered.length;
  $("table-caption").textContent = `Показано ${filtered.length} из ${state.records.length} записей · выборка до 25`;
}
const serviceDefinitions = [
  ["database", "PostgreSQL", "database", "Подключена"],
  ["opa", "Проверка политик", "shield", "Доступна"],
  ["collector", "Сборщик", "scan", "Зарегистрирован"],
  ["policy", "Подпись политики", "check", "Действительна"],
  ["control", "Аварийный контроль", "lock", "Разрешено"],
];
function renderServices(services) {
  $("services").replaceChildren();
  for (const [key, label, symbol, healthy] of serviceDefinitions) {
    const value = services[key] || "unavailable";
    const row = node("div", "service"); const name = node("span", "service-name");
    name.append(icon(symbol), document.createTextNode(label));
    const text = value === "unavailable" ? "Недоступно" : value === "blocked" ? "Блокировка" : healthy;
    row.append(name, node("span", `service-state ${value}`, text)); $("services").append(row);
  }
  $("control-banner").hidden = services.control !== "blocked";
}
function renderJob(job) {
  state.job = job;
  const running = job?.state === "running";
  $("run-check").disabled = !state.connected || running || ($("mode").value === "project" && !$("project-select").value);
  $("stop-app").disabled = running || !state.connected;
  $("mode").disabled = running; $("scenario").disabled = running;
  $("project-select").disabled = running;
  document.querySelectorAll(".project-scan,.project-remove").forEach(button => {button.disabled = running;});
  $("run-label").textContent = running ? "Проверяем…" : "Запустить проверку";
  $("job-banner").hidden = !job;
  if (!job) return;
  $("job-banner").className = `job-banner ${job.state}`;
  $("job-icon").replaceChildren(icon(running ? "activity" : ["completed","passed"].includes(job.state) ? "check" : "shield"));
  $("job-message").textContent = job.message;
  $("job-caption").textContent = job.mode === "project" && job.files_checked !== undefined
    ? `Проверено файлов: ${job.files_checked} · Замечаний: ${job.findings_count} · Статическая проверка`
    : job.snapshot_count !== undefined
    ? `Наблюдений: ${job.snapshot_count} · Инцидентов: ${job.incident_count} · ${sourceLabel(job.mode === "synthetic" ? "synthetic_fixture" : "local_kind")}`
    : "Результат появится здесь. Можно оставить эту страницу открытой.";
  $("job-detail").hidden = !job.audit_ids?.length && !job.report_id;
  $("job-detail").replaceChildren(document.createTextNode(job.report_id ? "Открыть отчёт " : "Открыть результат "), icon("arrow"));
  if (running && state.polling === null) state.polling = setTimeout(pollJob, 1500);
}
async function pollJob() {
  state.polling = null;
  try {
    const data = await api("/api/job"); renderJob(data.job);
    if (data.job?.state !== "running") await refresh();
  } catch (problem) {
    error(`${problem.message} Проверка могла продолжиться на сервере. Обновите данные, прежде чем запускать новую.`);
  }
}
async function refresh() {
  if (state.refresh) return;
  state.refresh = true; $("refresh").disabled = true;
  try {
    const data = await api("/api/overview");
    state.records = data.records; state.connected = true;
    $("metric-records").textContent = data.records.length;
    $("metric-incidents").textContent = data.records.filter(item => item.incident).length;
    $("metric-blocked").textContent = data.records.filter(item => item.status === "blocked").length;
    $("nav-count").textContent = data.records.length;
    $("updated-at").textContent = `Обновлено в ${formatTime(data.checked_at)}`;
    renderRecords(); renderServices(data.services); renderJob(data.job);
    await loadProjects();
    error(data.database_error ? "Не удалось прочитать аудит. Проверьте локальный PostgreSQL." :
      data.omitted_records ? `Не удалось показать ${data.omitted_records} записей: они не прошли проверку формата аудита.` : null);
  } catch (problem) {
    state.connected = false; $("run-check").disabled = true; error(problem.message);
    $("updated-at").textContent = "Подключение недоступно";
  } finally {state.refresh = false; $("refresh").disabled = false;}
}
$("run-check").addEventListener("click", async () => {
  $("run-check").disabled = true; error(null);
  const payload = {mode:$("mode").value};
  if (payload.mode === "synthetic") payload.scenario = $("scenario").value;
  if (payload.mode === "project") payload.project_id = $("project-select").value;
  try {
    const data = await api("/api/jobs", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    renderJob(data.job);
  } catch (problem) {
    error(`${problem.message} Запрос автоматически не повторяется; сначала обновите историю.`);
    // Keep the launch button disabled until an explicit refresh resolves an uncertain request.
  }
});
$("mode").addEventListener("change", () => {$("scenario").hidden = $("mode").value !== "synthetic"; $("project-select").hidden = $("mode").value !== "project"; renderJob(state.job);});
$("project-select").addEventListener("change", () => renderJob(state.job));
$("refresh").addEventListener("click", refresh);
$("search").addEventListener("input", event => {state.query = event.target.value.toLocaleLowerCase("ru").trim(); renderRecords();});
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(tab => tab.classList.toggle("active", tab === button)); renderRecords();
}));
const views = {
  overview:["Обзор", "Всё под наблюдением", "Состояние среды, результаты проверок и рекомендации — в одном месте."],
  history:["История проверок", "Каждая проверка — в истории", "Просматривайте сохранённые разборы, источники данных и решения политик."],
  system:["Состояние системы", "Среда под контролем", "Доступность локальных сервисов и ограничения текущего режима."],
  projects:["Мои проекты", "Проекты для проверки", "Добавляйте локальные папки и сохраняйте результаты проверок отдельно для каждого проекта."],
};
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
  const name = button.dataset.view; state.view = name; const copy = views[name];
  $("breadcrumb-view").textContent = copy[0]; $("page-title").replaceChildren(document.createTextNode(copy[1]), node("span", "", "."));
  $("page-subtitle").textContent = copy[2];
  document.querySelectorAll("[data-view]").forEach(item => {item.classList.toggle("active", item === button); item.setAttribute("aria-current", item === button ? "page" : "false");});
  $("main").className = name === "overview" ? "" : `${name}-view`;
  $("launch-panel").hidden = name === "system" || name === "projects";
  $("metrics").hidden = name === "system" || name === "projects";
  $("projects-panel").hidden = name !== "projects";
  $("content-grid").hidden = name === "projects";
  $("history-panel").hidden = name === "system";
  $("right-column").hidden = name === "history";
  $("history-title").textContent = name === "history" ? "История результатов" : "Последние результаты";
  if (name === "projects") loadProjects();
}));
const reasons = {
  kill_switch:"Аварийная блокировка включена. Рекомендация сохранена, дальнейший dry-run запрещён.",
  allowed:"Политика разрешила только описательный dry-run. Изменений в кластере не было.",
  opa_unavailable:"Сервис политик недоступен. Продолжение заблокировано.",
  policy_expired:"Срок подписанной политики истёк. Продолжение заблокировано.",
  policy_invalid:"Политика не прошла проверку подлинности. Продолжение заблокировано.",
  policy_missing:"Подписанная политика недоступна. Продолжение заблокировано.",
  python_denied:"Действие не прошло встроенные ограничения.",
};
function section(title) {const element = node("section", "detail-section"); element.append(node("h3", "", title)); return element;}
function pair(parent, key, value) {if (value === null || value === undefined || value === "") return; const list=node("dl","detail-kv"); list.append(node("dt","",key),node("dd","",value)); parent.append(list);}
function closeDetail() {
  $("drawer").hidden = true; $("drawer-backdrop").hidden = true; document.body.style.overflow = "";
  document.querySelector(".layout").inert = false; document.querySelector(".sidebar").inert = false;
  if (state.detailOrigin?.isConnected) state.detailOrigin.focus();
}
let detailSequence = 0;
async function openDetail(id, origin) {
  const sequence = ++detailSequence; state.detailOrigin = origin;
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false; document.body.style.overflow = "hidden";
  document.querySelector(".layout").inert = true; document.querySelector(".sidebar").inert = true;
  $("detail-body").replaceChildren(node("h2", "", "Загружаем разбор…")); $("close-detail").focus();
  try {
    const record = await api(`/api/audit/${encodeURIComponent(id)}`);
    if (sequence !== detailSequence || $("drawer").hidden) return;
    const body = $("detail-body"); body.replaceChildren();
    const title = node("h2", "", record.title); title.id = "detail-title";
    body.append(title, statusBadge(record.status), node("p", "detail-subtitle", `${sourceLabel(record.origin)} · ${formatTime(record.time,true)}`));
    const diagnosis = section("Что обнаружено");
    diagnosis.append(node("p", "", record.summary || (record.incident ? "Диагноз не сформирован. Смотрите причину остановки ниже." : "Признаков инцидента в этом наблюдении не обнаружено.")));
    for (const hypothesis of record.diagnosis?.hypotheses || []) {
      const item = node("div", "evidence-item"); item.append(node("span", "", `Уверенность: ${{high:"высокая",medium:"средняя",low:"низкая"}[hypothesis.confidence] || hypothesis.confidence}`), node("p", "", hypothesis.rationale)); diagnosis.append(item);
    }
    body.append(diagnosis);
    if (record.recommendation) {
      const recommendation = section("Рекомендация");
      recommendation.append(node("strong", "", record.recommendation.title), node("p", "", record.recommendation.rationale));
      for (const step of record.recommendation.explanation_steps) {
        if (step !== record.recommendation.rationale) recommendation.append(node("p", "", step));
      }
      body.append(recommendation);
    }
    const policy = section("Решение политики");
    policy.append(node("p", "", reasons[record.policy_reason] || (record.policy_reason ? `Продолжение остановлено: ${record.policy_reason}.` : "Для этой записи отдельное решение OPA не сохранено.")), node("div", "detail-callout", "Режим L0: AegisOps читает данные и описывает рекомендации. Действия в кластере не выполняются."));
    for (const message of record.errors) policy.append(node("p", "", message)); body.append(policy);
    if (record.evidence.length) {
      const evidence = section("На чём основан разбор");
      const sources = {pod_status:"Статус контейнера",k8s_event:"Событие Kubernetes",container_log:"Фрагмент логов",synthetic_fixture:"Учебные данные"};
      for (const item of record.evidence) {const card = node("div", "evidence-item"); card.append(node("span", "", sources[item.source] || item.source),node("p", "", item.summary === "b''" ? "В этом фрагменте логи пусты." : item.summary)); evidence.append(card);}
      body.append(evidence);
    }
    const metadata = section("Сведения о записи");
    pair(metadata,"Кластер",record.cluster); pair(metadata,"Namespace",record.namespace); pair(metadata,"Pod",record.pod);
    pair(metadata,"Версия политики",record.policy_version); pair(metadata,"Пакет",record.batch_id); pair(metadata,"ID аудита",record.id);
    const copy = node("button", "copy-button", "Скопировать ID аудита"); copy.addEventListener("click", async () => {try {await navigator.clipboard.writeText(record.id); toast("ID аудита скопирован");} catch {toast("Не удалось скопировать. ID показан выше.");}}); metadata.append(copy); body.append(metadata);
  } catch (problem) {$("detail-body").replaceChildren(node("h2", "", "Разбор недоступен"), node("p", "detail-callout", problem.message));}
}
$("close-detail").addEventListener("click", closeDetail);
$("drawer-backdrop").addEventListener("click", closeDetail);
$("job-detail").addEventListener("click", () => {
  if (state.job?.report_id) openProjectReport(state.job.report_id, $("job-detail"));
  else if (state.job?.audit_ids?.length) openDetail(state.job.audit_ids[0], $("job-detail"));
});
document.addEventListener("keydown", event => {
  if ($("drawer").hidden) return;
  if (event.key === "Escape") closeDetail();
  if (event.key === "Tab") {
    const elements = [...$("drawer").querySelectorAll("button:not([disabled])")];
    const first = elements[0], last = elements[elements.length-1];
    if (event.shiftKey && document.activeElement === first) {last.focus(); event.preventDefault();}
    else if (!event.shiftKey && document.activeElement === last) {first.focus(); event.preventDefault();}
  }
});
$("run-check").disabled = true;
$("stop-app").addEventListener("click", async () => {
  try {
    const result = await api("/api/stop", {method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    if (!result.stopped) throw new Error(result.error || "Не удалось завершить приложение");
    clearTimeout(state.polling); state.polling = null; state.connected = false;
    $("run-check").disabled = true; $("refresh").disabled = true; $("stop-app").disabled = true;
    error("Приложение остановлено. Эту вкладку можно закрыть. Для нового запуска откройте Start-AegisOps.cmd.");
    $("updated-at").textContent = "Приложение остановлено";
  } catch (problem) {error(problem.message);}
});

async function loadProjects() {
  try {
    const data = await api("/api/projects"); state.projects = data.projects;
    $("projects-error").hidden = true;
    const selected = $("project-select").value;
    $("project-select").replaceChildren();
    if (!data.projects.length) {const option=node("option","","Сначала добавьте проект"); option.value=""; $("project-select").append(option);}
    for (const project of data.projects) {const option=node("option","",project.name); option.value=project.id; $("project-select").append(option);}
    if (data.projects.some(project => project.id === selected)) $("project-select").value = selected;
    $("project-count").textContent = data.projects.length;
    $("projects-empty").hidden = data.projects.length !== 0;
    $("projects-list").replaceChildren();
    for (const project of data.projects) {
      const card=node("article","project-card");
      const heading=node("div","project-card-heading"), mark=node("span","object-mark"); mark.append(icon("box"));
      heading.append(mark,node("h3","",project.name));
      card.append(heading,node("p","project-path",project.path));
      const meta=node("div","project-meta");
      meta.append(project.last_report ? statusBadge(project.last_report.status) : node("span","",project.report_error ? "Отчёт недоступен" : "Ещё не проверялся"));
      if (project.last_report) meta.append(node("time","",formatTime(project.last_report.completed_at,true)));
      card.append(meta);
      const actions=node("div","project-actions"), run=node("button","primary-button project-scan","Проверить проект");
      run.addEventListener("click",async () => {
        run.disabled=true; error(null);
        try {const data=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:"project",project_id:project.id})}); renderJob(data.job); $("job-banner").scrollIntoView({block:"nearest",behavior:"smooth"});}
        catch(problem) {error(problem.message);}
      });
      const history=node("button","text-button","История"); history.append(icon("history"));
      history.addEventListener("click",() => projectHistory(project));
      const remove=node("button","project-remove","Убрать из списка"); remove.title="Файлы проекта и сохранённые отчёты останутся на диске";
      remove.addEventListener("click",async () => {
        try {await api("/api/projects/remove",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({project_id:project.id})}); $("project-history").hidden=true; await loadProjects(); toast("Проект убран из списка. Файлы не изменены.");}
        catch(problem) {error(problem.message);}
      });
      actions.append(run,history,remove); card.append(actions); $("projects-list").append(card);
    }
    renderJob(state.job);
  } catch(problem) {$("projects-error").textContent=problem.message; $("projects-error").hidden=false;}
}
$("project-form").addEventListener("submit",async event => {
  event.preventDefault(); $("add-project").disabled=true;
  try {
    await api("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:$("project-name").value.trim(),path:$("project-path").value.trim()})});
    $("project-form").reset(); await loadProjects(); toast("Проект добавлен. Теперь его можно проверить.");
  } catch(problem) {$("projects-error").textContent=problem.message; $("projects-error").hidden=false;}
  finally {$("add-project").disabled=false;}
});
let historySequence=0;
async function projectHistory(project) {
  const sequence=++historySequence;
  $("project-history").hidden=false; $("project-history-title").textContent=`История: ${project.name}`;
  $("project-history-list").replaceChildren(node("p","detail-callout","Загружаем отчёты…"));
  try {
    const data=await api(`/api/projects/${project.id}`); if (sequence!==historySequence) return;
    $("project-history-list").replaceChildren();
    if (!data.reports.length) $("project-history-list").append(node("p","detail-callout","У этого проекта пока нет проверок."));
    for (const report of data.reports) {
      const row=node("div","project-history-row"); row.append(statusBadge(report.status),node("time","",formatTime(report.completed_at,true)),node("span","",`Файлов: ${report.files_checked}`));
      const open=node("button","text-button","Открыть отчёт"); open.append(icon("arrow")); open.addEventListener("click",()=>openProjectReport(report.id,open)); row.append(open); $("project-history-list").append(row);
    }
    $("project-history").scrollIntoView({block:"nearest",behavior:"smooth"});
  } catch(problem) {$("project-history-list").replaceChildren(node("p","detail-callout",problem.message));}
}
async function openProjectReport(id,origin) {
  const sequence=++detailSequence; state.detailOrigin=origin;
  $("drawer").hidden=false; $("drawer-backdrop").hidden=false; document.body.style.overflow="hidden";
  document.querySelector(".layout").inert=true; document.querySelector(".sidebar").inert=true;
  $("detail-body").replaceChildren(node("h2","","Загружаем отчёт…")); $("close-detail").focus();
  try {
    const report=await api(`/api/project-reports/${id}`); if(sequence!==detailSequence || $("drawer").hidden) return;
    const project=state.projects.find(item=>item.id===report.project_id);
    const body=$("detail-body"); body.replaceChildren(); const title=node("h2","",project?.name || "Проверка проекта"); title.id="detail-title";
    body.append(title,statusBadge(report.status),node("p","detail-subtitle",formatTime(report.completed_at,true)),node("div","detail-callout","Статическая проверка форматов и выбранных настроек. Код и тесты проекта не запускались. Этот отчёт не заменяет полный аудит или проверку поведения приложения."));
    const stats=node("div","report-stats");
    for (const [value,label] of [[report.files_seen,"Файлов найдено"],[report.files_checked,"Файлов проверено"],[report.skipped,"Пропусков: файлы и папки"]]) {const cell=node("div","report-stat"); cell.append(node("strong","",value),node("span","",label)); stats.append(cell);} body.append(stats);
    const summary=section("Область проверки");
    pair(summary,"Версия правил",report.rules_version || "local-static-v1");
    summary.append(node("p","",report.stacks.length ? report.stacks.join(" · ") : "Стек не определён"));
    summary.append(node("p","","Синтаксис Python и файлов JSON/YAML/TOML; несколько правил Kubernetes и Docker Compose. JavaScript/TypeScript — только определение по структуре."));
    if (report.rules_version === "local-static-v2") summary.append(node("p","","Kubernetes: основные, init- и временные контейнеры, Pod-шаблоны и List. Compose: привилегии, пространства хоста, образы и прямое подключение Docker socket. Настройки не исправляются автоматически."));
    if (report.partial) summary.append(node("p","","Результат частичный: часть файлов недоступна, использует неподдержанные конструкции или достигнут лимит проверки."));
    body.append(summary);
    const findings=section(`Замечания: ${report.findings.length}`);
    if (!report.findings.length) findings.append(node("p","","В рамках выполненных проверок замечаний не найдено. Это не подтверждает отсутствие ошибок в проекте."));
    for (const item of report.findings) {
      const card=node("div",`finding-card ${item.severity}`);
      card.append(node("span","finding-severity",item.severity === "error" ? "Ошибка формата" : "Требует внимания"),node("strong","",`${item.file}${item.line ? ` · строка ${item.line}` : ""}`));
      if (item.location) card.append(node("code","finding-location",item.location));
      card.append(node("p","",item.message));
      if (item.recommendation) {const action=node("div","finding-action"); action.append(node("strong","","Что сделать"),node("p","",item.recommendation)); card.append(action);}
      findings.append(card);
    } body.append(findings);
    if (report.rules_version === "local-static-v2") findings.append(node("p","detail-subtitle","document — номер YAML-документа с 1; индексы в [ ] — с 0; services[#N] — сервис по порядку с 1. Рекомендации требуют проверки с учётом назначения приложения."));
    pair(body,"ID отчёта",report.id);
  } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden) $("detail-body").replaceChildren(node("h2","","Отчёт недоступен"),node("p","detail-callout",problem.message));}
}

refresh();
