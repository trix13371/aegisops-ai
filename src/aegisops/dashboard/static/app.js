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
const state = {records:[], projects:[], filter:"all", query:"", projectQuery:"", view:"projects", connected:false, job:null, polling:null, refresh:false, detailOrigin:null, detailProjectFocus:null};
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
  document.querySelectorAll(".project-scan,.project-remove").forEach(button => {button.disabled = running || button.dataset.submitted === "true";});
  document.querySelectorAll(".project-ai-start,.runner-start").forEach(button => {button.disabled = running || button.dataset.compatible === "false" || !document.getElementById(button.dataset.consentId)?.checked;});
  $("run-label").textContent = running ? "Проверяем…" : $("mode").value === "project" ? "Выбрать проверку" : "Запустить проверку";
  $("job-banner").hidden = !job;
  if (!job) return;
  $("job-banner").className = `job-banner ${job.state}`;
  $("job-icon").replaceChildren(icon(running ? "activity" : ["completed","passed"].includes(job.state) ? "check" : "shield"));
  $("job-message").textContent = job.message;
  $("job-caption").textContent = job.mode === "project_runner" ? "Проверка кода в контейнере · без сети и ключей AegisOps"
    : job.mode === "project_review_ai" ? "Разбор сводного отчёта через OpenRouter · без исходников и логов"
    : job.mode === "project_ai" ? "Разбор сводки через OpenRouter · без передачи исходников"
    : job.mode === "project" && job.files_checked !== undefined
    ? `Проверено файлов: ${job.files_checked} · Замечаний: ${job.findings_count} · Статическая проверка`
    : job.snapshot_count !== undefined
    ? `Наблюдений: ${job.snapshot_count} · Инцидентов: ${job.incident_count} · ${sourceLabel(job.mode === "synthetic" ? "synthetic_fixture" : "local_kind")}`
    : "Результат появится здесь. Можно оставить эту страницу открытой.";
  $("job-detail").hidden = !job.audit_ids?.length && !job.report_id && !job.run_id && !job.review_id;
  $("job-detail").replaceChildren(document.createTextNode(job.review_id ? "Открыть сводный отчёт " : job.run_id ? "Результаты запуска " : job.report_id ? "Открыть отчёт " : "Открыть результат "), icon("arrow"));
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
    const [overviewResult] = await Promise.allSettled([api("/api/overview"), loadProjects()]);
    if (overviewResult.status === "rejected") throw overviewResult.reason;
    const data = overviewResult.value;
    state.records = data.records; state.connected = true;
    $("metric-records").textContent = data.records.length;
    $("metric-incidents").textContent = data.records.filter(item => item.incident).length;
    $("metric-blocked").textContent = data.records.filter(item => item.status === "blocked").length;
    $("nav-count").textContent = data.records.length;
    $("updated-at").textContent = `Обновлено в ${formatTime(data.checked_at)}`;
    renderRecords(); renderServices(data.services); renderJob(data.job);
    error(data.database_error ? "Не удалось прочитать аудит. Проверьте локальный PostgreSQL." :
      data.omitted_records ? `Не удалось показать ${data.omitted_records} записей: они не прошли проверку формата аудита.` : null);
  } catch (problem) {
    state.connected = false; $("run-check").disabled = true; error(problem.message);
    $("updated-at").textContent = "Подключение недоступно";
  } finally {state.refresh = false; $("refresh").disabled = false;}
}
$("run-check").addEventListener("click", async () => {
  if($("mode").value==="project") {
    const project=state.projects.find(item=>item.id===$("project-select").value);
    if(project)chooseProjectCheck(project,$("run-check"));return;
  }
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
  history:["Кластер и демо", "Результаты кластера и демо", "Сохранённые наблюдения кластера и учебные сценарии. История проектов — на их карточках."],
  system:["Состояние системы", "Среда под контролем", "Доступность локальных сервисов и ограничения текущего режима."],
  projects:["Мои проекты", "Проверьте свой проект", "Выберите папку, запустите нужную проверку и разберите результаты."],
};
function showView(name) {
  state.view = name; const copy = views[name];
  $("breadcrumb-view").textContent = copy[0]; $("page-title").replaceChildren(document.createTextNode(copy[1]), node("span", "", "."));
  $("page-subtitle").textContent = copy[2];
  document.querySelectorAll("[data-view]").forEach(item => {const active=item.dataset.view===name;item.classList.toggle("active",active);if(active)item.setAttribute("aria-current","page");else item.removeAttribute("aria-current");});
  $("main").className = name === "overview" ? "" : `${name}-view`;
  $("launch-panel").hidden = name === "system" || name === "projects";
  $("metrics").hidden = name === "system" || name === "projects";
  $("projects-panel").hidden = name !== "projects";
  $("content-grid").hidden = name === "projects";
  $("history-panel").hidden = name === "system";
  $("right-column").hidden = name === "history";
  $("new-project").hidden = name !== "projects";
  $("history-title").textContent = name === "history" ? "История результатов" : "Последние результаты";
}
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
  showView(button.dataset.view);
  if (state.view === "projects") loadProjects();
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
function canFocus(element) {return element?.isConnected && !element.disabled && element.getClientRects().length;}
function focusProjectControl(preference) {
  const controls=[...document.querySelectorAll("[data-project-id]")].filter(button=>button.dataset.projectId===preference?.id && canFocus(button));
  const target=controls.find(button=>button.dataset.projectAction===preference?.action) || controls.find(button=>button.dataset.projectAction==="results");
  (target || document.querySelector('[data-view="projects"]')).focus();
}
function closeDetail() {
  ++detailSequence;
  $("drawer").hidden = true; $("drawer-backdrop").hidden = true; document.body.style.overflow = "";
  document.querySelector(".layout").inert = false; document.querySelector(".sidebar").inert = false;
  if (canFocus(state.detailOrigin)) state.detailOrigin.focus();
  else focusProjectControl(state.detailProjectFocus);
}
let detailSequence = 0;
async function openDetail(id, origin) {
  const sequence = openRunnerDrawer("Загружаем разбор…",origin);
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
  } catch (problem) {if(sequence===detailSequence && !$("drawer").hidden) {const heading=node("h2","","Разбор недоступен");heading.id="detail-title";$("detail-body").replaceChildren(heading,node("p","detail-callout",problem.message));}}
}
$("close-detail").addEventListener("click", closeDetail);
$("drawer-backdrop").addEventListener("click", closeDetail);
$("job-detail").addEventListener("click", () => {
  if (state.job?.review_id) openReview(state.job.review_id, $("job-detail"));
  else if (state.job?.run_id) openRunReport(state.job.run_id, $("job-detail"));
  else if (state.job?.report_id) openProjectReport(state.job.report_id, $("job-detail"));
  else if (state.job?.audit_ids?.length) openDetail(state.job.audit_ids[0], $("job-detail"));
});
document.addEventListener("keydown", event => {
  if ($("drawer").hidden) return;
  if (event.key === "Escape") closeDetail();
  if (event.key === "Tab") {
    const elements = [...$("drawer").querySelectorAll("button,input,select,summary,[tabindex]")].filter(element=>!element.disabled && element.tabIndex>=0 && element.getClientRects().length);
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
    const data=await api("/api/projects");state.projects=data.projects;
    $("projects-error").hidden=true;
    const selected=$("project-select").value;
    $("project-select").replaceChildren();
    if(!data.projects.length) {const option=node("option","","Сначала добавьте проект");option.value="";$("project-select").append(option);}
    for(const project of data.projects) {const option=node("option","",project.name);option.value=project.id;$("project-select").append(option);}
    if(data.projects.some(project=>project.id===selected))$("project-select").value=selected;
    $("project-count").textContent=data.projects.length;
    renderProjects();renderJob(state.job);
  } catch(problem) {$("projects-error").textContent=problem.message;$("projects-error").hidden=false;}
}
function projectButton(project,action,label,className) {
  const button=node("button",className,label);button.dataset.projectId=project.id;button.dataset.projectAction=action;
  button.setAttribute("aria-label",`${label}: ${project.name}`);return button;
}
function renderProjects() {
  const focused=document.activeElement?.dataset.projectId ? {id:document.activeElement.dataset.projectId,action:document.activeElement.dataset.projectAction} : null;
  const projects=state.projects.filter(project=>`${project.name} ${project.path}`.toLocaleLowerCase("ru").includes(state.projectQuery));
  $("projects-summary").textContent=state.projectQuery ? `Найдено ${projects.length} из ${state.projects.length}` : `Проектов: ${projects.length}`;
  $("projects-empty").hidden=state.projects.length!==0;
  $("projects-list").replaceChildren();
  if(!projects.length && state.projects.length)$("projects-list").append(node("p","project-no-matches","Ничего не найдено. Попробуйте другое название или путь."));
  for(const project of projects) {
    const card=node("article","project-card");
    const top=node("div","project-card-top"),heading=node("div","project-card-heading"),mark=node("span","object-mark");mark.append(icon("box"));
    heading.append(mark,node("h3","",project.name));
    const options=node("details","project-options"),summary=node("summary","","Настройки");
    options.append(summary,node("p","project-path",project.path));
    const remove=projectButton(project,"remove","Убрать из списка","project-remove");
    const removeNote=node("small","","Папка и отчёты останутся на диске.");
    remove.addEventListener("click",async()=>{
      remove.disabled=true;
      try {const data=await api("/api/projects/remove",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({project_id:project.id})});if(data.error)throw new Error(data.error);await loadProjects();toast("Проект убран из списка. Файлы не изменены.");}
      catch(problem) {error(`${problem.message} Обновите список перед повторным действием.`);}
    });
    options.append(remove,removeNote);top.append(heading,options);card.append(top);
    const meta=node("div","project-status-row");meta.append(node("span","project-status-label","Файлы и настройки"));
    meta.append(project.last_report ? statusBadge(project.last_report.status) : node("span","status neutral",project.report_error ? "Отчёт недоступен" : "Проверок пока нет"));card.append(meta);
    card.append(node("p","project-hint",project.last_report ? `Последний отчёт: ${formatTime(project.last_report.completed_at,true)}. Тесты и сводки — в результатах.` : "Начните с проверки файлов или запустите готовые тесты проекта."));
    const actions=node("div","project-actions"),choose=projectButton(project,"check","Выбрать проверку","primary-button project-scan"),results=projectButton(project,"results","Результаты и история","secondary-button project-results");
    choose.prepend(icon("play"));choose.append(icon("chevron"));results.prepend(icon("history"));
    choose.addEventListener("click",()=>chooseProjectCheck(project,choose));results.addEventListener("click",()=>projectResults(project,results));
    choose.disabled=state.job?.state==="running";remove.disabled=state.job?.state==="running";
    actions.append(choose,results);card.append(actions);$("projects-list").append(card);
  }
  if(focused)focusProjectControl(focused);
}
function showProjectForm(visible) {
  $("project-add-panel").hidden=!visible;$("new-project").setAttribute("aria-expanded",String(visible));
  if(visible) {$("project-add-panel").scrollIntoView({block:"nearest"});$("project-name").focus();}
  else $("new-project").focus();
}
$("new-project").addEventListener("click",()=>showProjectForm($("project-add-panel").hidden));
$("cancel-add-project").addEventListener("click",()=>showProjectForm(false));
$("project-search").addEventListener("input",event=>{state.projectQuery=event.target.value.toLocaleLowerCase("ru").trim();renderProjects();});
$("project-form").addEventListener("submit",async event=>{
  event.preventDefault();$("add-project").disabled=true;
  try {
    const data=await api("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:$("project-name").value.trim(),path:$("project-path").value.trim()})});
    if(data.error)throw new Error(data.error);
    $("project-form").reset();$("project-search").value="";state.projectQuery="";showProjectForm(false);await loadProjects();toast("Проект добавлен. Нажмите «Выбрать проверку» на его карточке.");
  } catch(problem) {$("projects-error").textContent=problem.message;$("projects-error").hidden=false;}
  finally {$("add-project").disabled=false;}
});
function drawerBack(body,label,action) {
  const back=node("button","drawer-back",label);back.prepend(icon("chevron"));back.addEventListener("click",action);
  body.insertBefore(back,body.firstChild);
}
function actionOption(title,description,tag,symbol,action) {
  const button=node("button","action-option"),copy=node("span","action-option-copy"),mark=node("span","action-option-icon");mark.append(icon(symbol));
  copy.append(node("strong","",title),node("small","",description));const arrow=node("span","action-option-arrow");arrow.append(icon("chevron"));button.append(mark,copy,node("span","action-option-tag",tag),arrow);button.addEventListener("click",action);return button;
}
function chooseProjectCheck(project,origin) {
  openRunnerDrawer(`Как проверить «${project.name}»?`,origin);const body=$("detail-body");
  body.append(node("p","drawer-intro","Выберите, что хотите узнать. На следующем экране будет описание и кнопка запуска."));
  body.append(actionOption("Файлы и настройки","Проверка форматов и настроек контейнеров. Код проекта не запускается.","Без Docker","scan",()=>staticCheck(project,origin)),actionOption("Тесты Python","Проверка Python и запуск существующих тестов в отдельном контейнере.","Нужен Docker","play",()=>projectRuns(project,origin,"python")),actionOption("Тесты JavaScript / TypeScript","Проверка TypeScript и тесты Node.js или Vitest. Окружение подбирается по package.json.","Нужен Docker","play",()=>projectRuns(project,origin,"javascript")));
  body.append(node("p","check-note","Эти проверки бесплатны: нейросеть здесь не вызывается. AI-разбор доступен отдельно в сохранённом отчёте."));
}
function staticCheck(project,origin) {
  const sequence=openRunnerDrawer("Проверить файлы и настройки",origin),body=$("detail-body");
  drawerBack(body,"К выбору проверки",()=>chooseProjectCheck(project,origin));
  body.append(node("p","drawer-intro",project.name),node("p","","Проверим синтаксис Python, JSON, YAML и TOML, а также выбранные настройки Kubernetes и Docker Compose."),node("p","check-note","Код и тесты не запускаются, файлы проекта не изменяются. Docker и нейросеть не нужны. Это не полная проверка работоспособности приложения."));
  const run=node("button","primary-button project-scan","Проверить файлы и настройки");run.prepend(icon("scan"));run.disabled=state.job?.state==="running";
  if(run.disabled)body.append(node("p","detail-callout","Сейчас выполняется другая проверка. Дождитесь её завершения."));
  run.addEventListener("click",async()=>{
    run.disabled=true;run.dataset.submitted="true";
    try {const data=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:"project",project_id:project.id})});if(!data.job)throw new Error(data.error || "Запуск не подтверждён");renderJob(data.job);if(sequence===detailSequence && !$("drawer").hidden)closeDetail();$("job-banner").scrollIntoView({block:"nearest",behavior:"smooth"});}
    catch(problem) {if(sequence===detailSequence && !$("drawer").hidden)body.append(node("p","detail-callout",`${problem.message} Запрос не повторяется. Проверьте результаты или обновите страницу.`));}
  });body.append(run);
}
async function projectResults(project,origin,selected="files") {
  const sequence=openRunnerDrawer(project.name,origin),body=$("detail-body");
  body.append(node("p","drawer-intro","Все сохранённые результаты этого проекта. Просмотр не запускает новые проверки."));
  const tabs=node("div","project-tabs");tabs.setAttribute("role","tablist");tabs.setAttribute("aria-label","Виды результатов проекта");
  const panel=node("div","tab-panel result-list");panel.id="project-results-panel";panel.setAttribute("role","tabpanel");panel.tabIndex=0;
  const types=[["files","Файлы и настройки"],["tests","Тесты"],["reviews","Сводные отчёты"]];let tabSequence=0;
  const load=async(kind)=>{
    const request=++tabSequence;
    for(const tab of tabs.children) {const active=tab.dataset.kind===kind;tab.classList.toggle("active",active);tab.setAttribute("aria-selected",String(active));tab.tabIndex=active ? 0 : -1;}
    panel.setAttribute("aria-labelledby",`project-tab-${kind}`);panel.setAttribute("aria-busy","true");
    const loading=node("p","detail-subtitle","Загружаем результаты…");loading.setAttribute("role","status");panel.replaceChildren(loading);
    try {
      const path=kind==="files" ? `/api/projects/${project.id}` : kind==="tests" ? `/api/project-runs/${project.id}` : `/api/project-reviews/${project.id}`;
      const data=await api(path);if(sequence!==detailSequence || request!==tabSequence || $("drawer").hidden)return;panel.replaceChildren();
      const items=kind==="files" ? data.reports : kind==="tests" ? data.runs : data.reviews;
      if(kind==="reviews") {
        panel.append(node("p","check-note","Сводный отчёт объединяет выбранные проверки файлов и результаты тестов. AI-разбор — только по отдельному согласию."));
        const create=node("button","secondary-button","Собрать сводный отчёт");create.prepend(icon("layers"));create.addEventListener("click",()=>projectReviews(project,origin));panel.append(create);
      }
      if(!items.length) {
        panel.append(node("h3","result-empty-title",kind==="reviews" ? "Сводных отчётов пока нет" : "Здесь появятся результаты"),node("p","",kind==="reviews" ? "Сначала сохраните проверку файлов или запуск тестов, затем объедините их в сводку." : "Выберите проверку на карточке проекта. Готовый результат сохранится здесь."));
        if(kind!=="reviews") {const choose=node("button","secondary-button","Выбрать проверку");choose.addEventListener("click",()=>chooseProjectCheck(project,origin));panel.append(choose);}
      }
      if(items.length>=2 && kind!=="reviews") {
        const compare=node("details","result-comparison"),summary=node("summary","","Сравнить два результата");compare.append(summary,comparisonControls(items.map(item=>({id:kind==="files" ? item.id : item.claim.id,time:kind==="files" ? item.completed_at : item.claim.created_at})),kind==="files" ? "project" : "run",origin));panel.append(compare);
      }
      for(const item of items) {
        const row=node("div","result-row"),copy=node("div","result-row-copy"),open=node("button","secondary-button result-open","Открыть"),time=kind==="files" ? item.completed_at : kind==="tests" ? item.claim.created_at : item.created_at;
        if(kind==="files")copy.append(statusBadge(item.status),node("small","",`Файлов проверено: ${item.files_checked}`));
        else if(kind==="tests")copy.append(node("strong","",runLabels[item.result?.status || "pending"]),node("small","",environmentLabels[item.claim.environment || "minimal"]));
        else copy.append(reviewBadge(item.status));
        copy.append(node("time","",formatTime(time,true)));open.append(icon("arrow"));open.setAttribute("aria-label",`Открыть ${kind==="files" ? "проверку файлов" : kind==="tests" ? "тесты" : "сводный отчёт"} от ${formatTime(time,true)}`);
        open.addEventListener("click",()=>kind==="files" ? openProjectReport(item.id,origin) : kind==="tests" ? openRunReport(item.claim.id,origin) : openReview(item.id,origin));row.append(copy,open);panel.append(row);
      }
    } catch(problem) {if(sequence===detailSequence && request===tabSequence && !$("drawer").hidden)panel.replaceChildren(node("p","detail-callout","Не удалось прочитать историю. Обновите данные или откройте проект снова. Новые проверки не запускались."));}
    finally {if(sequence===detailSequence && request===tabSequence)panel.removeAttribute("aria-busy");}
  };
  for(const [kind,title] of types) {
    const tab=node("button","project-tab",title);tab.id=`project-tab-${kind}`;tab.dataset.kind=kind;tab.setAttribute("role","tab");tab.setAttribute("aria-controls",panel.id);tab.addEventListener("click",()=>load(kind));
    tab.addEventListener("keydown",event=>{
      const positions={ArrowRight:1,ArrowLeft:-1,Home:"first",End:"last"};if(!Object.hasOwn(positions,event.key))return;event.preventDefault();
      const buttons=[...tabs.children],position=positions[event.key],index=position==="first" ? 0 : position==="last" ? buttons.length-1 : (buttons.indexOf(tab)+position+buttons.length)%buttons.length;
      buttons[index].focus();load(buttons[index].dataset.kind);
    });tabs.append(tab);
  }
  body.append(tabs,panel);await load(selected);
}
async function openProjectReport(id,origin) {
  const sequence=openRunnerDrawer("Загружаем отчёт…",origin);
  try {
    const report=await api(`/api/project-reports/${id}`); if(sequence!==detailSequence || $("drawer").hidden) return;
    const project=state.projects.find(item=>item.id===report.project_id);
    const body=$("detail-body"); body.replaceChildren(); const title=node("h2","",project?.name || "Проверка проекта"); title.id="detail-title";
    body.append(title,statusBadge(report.status),node("p","detail-subtitle",formatTime(report.completed_at,true)),node("div","detail-callout","Статическая проверка форматов и выбранных настроек. Код и тесты проекта не запускались. Этот отчёт не заменяет полный аудит или проверку поведения приложения."));
    if(project)drawerBack(body,"К результатам проекта",()=>projectResults(project,origin,"files"));
    const stats=node("div","report-stats");
    for (const [value,label] of [[report.files_seen,"Файлов найдено"],[report.files_checked,"Файлов проверено"],[report.skipped,"Пропусков: файлы и папки"]]) {const cell=node("div","report-stat"); cell.append(node("strong","",value),node("span","",label)); stats.append(cell);} body.append(stats);
    const summary=section("Область проверки");
    pair(summary,"Версия правил",report.rules_version || "local-static-v1");
    summary.append(node("p","",report.stacks.length ? report.stacks.join(" · ") : "Стек не определён"));
    summary.append(node("p","","Синтаксис Python и файлов JSON/YAML/TOML; несколько правил Kubernetes и Docker Compose. JavaScript/TypeScript — только определение по структуре."));
    if (report.rules_version === "local-static-v2") summary.append(node("p","","Kubernetes: основные, init- и временные контейнеры, Pod-шаблоны и List. Compose: привилегии, пространства хоста, образы и прямое подключение Docker socket. Настройки не исправляются автоматически."));
    if (report.partial) summary.append(node("p","","Результат частичный: часть файлов недоступна, использует неподдержанные конструкции или достигнут лимит проверки."));
    const scope=node("details","runner-advanced");scope.append(node("summary","","Что входит в проверку"),summary);
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
    body.append(scope);
    const aiPanel=await projectAiSection(report,sequence);
    if(sequence!==detailSequence || $("drawer").hidden) return;
    body.append(aiPanel);
    const metadata=node("details","runner-advanced");metadata.append(node("summary","","Технические сведения"));pair(metadata,"ID отчёта",report.id);body.append(metadata);
  } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden) {const heading=node("h2","","Отчёт недоступен");heading.id="detail-title";$("detail-body").replaceChildren(heading,node("p","detail-callout",problem.message));}}
}

async function projectAiSection(report,sequence) {
  const panel=section("Нейросетевой разбор"); panel.classList.add("project-ai-panel");
  panel.append(node("p","","Модель объясняет сводку найденных замечаний. Исходники она не читает; это не ревью кода и не запуск тестов."));
  try {
    const data=await api(`/api/project-ai/${report.id}`), analysis=data.analysis;
    pair(panel,"Провайдер / модель",`OpenRouter · ${data.model}`);
    if (analysis.status === "completed") {
      panel.append(node("span","status passed","Ответ модели получен"),node("p","ai-summary",analysis.response.summary));
      const priorities={high:"Высокий",medium:"Средний",low:"Низкий"};
      for (const action of analysis.response.actions) {
        const card=node("div","ai-action"); card.append(node("strong","",`Приоритет: ${priorities[action.priority]}`),node("p","",action.explanation),node("strong","","Следующий шаг"),node("p","",action.next_step));
        const matches=report.findings.filter(item=>item.code===action.code);
        card.append(node("small","finding-location",`В отчёте: ${matches.slice(0,3).map(item=>`${item.file}${item.location ? ` · ${item.location}` : ""}`).join("; ")}${matches.length>3 ? `; ещё ${matches.length-3}` : ""}`));
        panel.append(card);
      }
      panel.append(node("p","detail-callout",analysis.response.limitations),node("p","detail-subtitle","Это мнение модели по сводке. Рекомендации требуют ручной проверки; файлы и настройки не изменялись."));
    } else if (analysis.status === "pending") {
      panel.append(node("p","detail-callout","Разбор уже запрошен. Дождитесь завершения и откройте отчёт снова. Если приложение прервалось, состояние запроса неизвестно; повтор автоматически не отправляется."));
    } else if (analysis.status === "failed") {
      panel.append(node("p","detail-callout","Ответ модели не подтверждён. Возможны ошибка подключения, лимит ключа или неподдержанный ответ. Статический отчёт сохранён. Повтора нет: при обрыве запрос мог быть оплачен. Проверьте отдельный ключ проекта и его лимит; для новой попытки создайте новый статический отчёт."));
    } else {
      panel.append(node("p","","В OpenRouter отправятся только версия правил, статус, число проверенных файлов/пропусков и коды замечаний с их количеством. Названия, пути, строки исходников и значения конфигурации не отправляются."));
      const preview=node("details","ai-preview"); preview.append(node("summary","","Посмотреть отправляемую сводку"),node("pre","",JSON.stringify(data.payload,null,2))); panel.append(preview);
      panel.append(node("p","detail-subtitle","Один платный запрос, до 1600 выходных токенов. Используется отдельный ключ AegisOps с общим лимитом до $5. Для одного отчёта — одна попытка, без автоматического повтора."));
      const label=node("label","ai-consent"), consent=node("input"); consent.type="checkbox"; consent.id=`ai-consent-${report.id}`;
      label.append(consent,document.createTextNode("Отправить эту сводку в OpenRouter и выполнить один платный разбор"));
      const run=node("button","primary-button project-ai-start","Разобрать с нейросетью"); run.dataset.consentId=consent.id; run.disabled=true;
      consent.addEventListener("change",()=>{run.disabled=!consent.checked || state.job?.state === "running";});
      run.addEventListener("click",async()=>{
        run.disabled=true;run.dataset.compatible="false";consent.disabled=true;
        try {
          const result=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:"project_ai",report_id:report.id,consent:data.profile})});
          if(!result.job)throw new Error(result.error || "Состояние запроса не подтверждено");
          renderJob(result.job);if(sequence===detailSequence && !$("drawer").hidden)closeDetail();toast("Состояние запроса показано над списком проектов.");
        } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden)panel.append(node("p","detail-callout",`${problem.message} Обновите отчёт перед дальнейшими действиями; запрос не повторяется автоматически.`));}
      });
      panel.append(label,run);
    }
  } catch(problem) {panel.append(node("p","detail-callout","Состояние нейросетевого разбора недоступно. Новый запрос не отправлен. Откройте отчёт повторно после проверки локального хранилища."));}
  return panel;
}

function openRunnerDrawer(title,origin) {
  const sequence=++detailSequence;
  if(origin?.isConnected && !$("drawer").contains(origin)) {
    state.detailOrigin=origin;
    state.detailProjectFocus=origin.dataset.projectId ? {id:origin.dataset.projectId,action:origin.dataset.projectAction} : null;
  }
  $("drawer").hidden=false; $("drawer-backdrop").hidden=false; document.body.style.overflow="hidden";
  document.querySelector(".layout").inert=true; document.querySelector(".sidebar").inert=true;
  const heading=node("h2","",title); heading.id="detail-title"; $("detail-body").replaceChildren(heading);
  $("drawer").scrollTop=0;
  $("close-detail").focus(); return sequence;
}
const environmentLabels={minimal:"Базовое · Ruff и pytest", "python-data-v1":"Данные · Pydantic и PyYAML", "node-js-v1":"Node.js · встроенные тесты", "node-vitest-v1":"Vitest · подготовленные зависимости"};
const toolLabels={ruff:"Ruff · ошибки Python",pytest:"pytest · тесты проекта",typescript:"TypeScript · синтаксис и типы", "node-test":"Node.js · тесты проекта",vitest:"Vitest · тесты проекта"};
const runLabels={passed:"Проверки пройдены",issues:"Есть замечания",error:"Ошибка проверки",timeout:"Превышено время",not_supported:"Профиль не подошёл",pending:"Результат неизвестен"};
const compatibilityIssueLabels={package_unsupported:"Формат или зависимости package.json не поддерживаются.",dependencies_unavailable:"В подготовленном окружении нет всех объявленных зависимостей.",framework_mismatch:"Выбран другой тестовый инструмент.",environment_directive:"Обнаружена директива смены окружения теста. Поддерживается только фиксированное Node-окружение."};
const compatibilityReasonLabels={package_missing:"package.json не найден; доступны встроенные тесты Node.js.",package_invalid:"package.json не удалось безопасно прочитать или разобрать.",dependency_limit:"Превышен лимит объявлений зависимостей.",invalid_dependency:"Некорректное объявление зависимости.",workspace_project:"Workspaces пока не поддерживаются.",non_registry_dependency:"Локальные, git и alias-зависимости пока не поддерживаются.",jest_unsupported:"Jest пока не поддерживается.",mixed_frameworks:"Объявлены разные тестовые инструменты; автоматического выбора нет.",additional_dependencies:"Есть зависимости за пределами подготовленного набора.",dependency_versions_unverified:"Используются закреплённые версии окружения; ограничения версий и lock проекта не воспроизводятся."};
function renderCompatibility(panel,data) {
  const frameworks={node:"Встроенные тесты Node.js",vitest:"Vitest",jest:"Jest",mixed:"Несколько инструментов",unknown:"Не определён"};
  pair(panel,"Инструмент",frameworks[data.framework] || "Не определён");
  for(const reason of data.reasons || [])panel.append(node("p","detail-subtitle",compatibilityReasonLabels[reason] || "Совместимость ограничена."));
  if(data.dependencies?.length)panel.append(node("p","detail-subtitle",`Объявленные пакеты: ${data.dependencies.slice(0,8).join(", ")}${data.dependencies.length>8 ? ` и ещё ${data.dependencies.length-8}` : ""}`));
}
async function projectRuns(project,origin,language="python") {
  const js=language==="javascript";
  const sequence=openRunnerDrawer(js ? "Запустить тесты JavaScript / TypeScript" : "Запустить тесты Python",origin),body=$("detail-body");
  drawerBack(body,"К выбору проверки",()=>chooseProjectCheck(project,origin));
  body.append(node("p","drawer-intro",project.name),node("p","",js ? "Проверим синтаксис и типы, затем запустим готовые тесты Node.js или Vitest." : "Проверим ошибки Python с Ruff, затем запустим готовые тесты pytest."),node("p","check-note","Будет выполнен код проекта в отдельном контейнере Docker, без сети и ключей AegisOps. Исходная папка не изменяется. Нейросеть не вызывается."));
  const advanced=node("details","runner-advanced");advanced.append(node("summary","","Окружение и ограничения проверки"));
  advanced.append(node("p","","Лимиты: 512 МБ, 1 CPU, 64 процесса; инструменты — до 60 секунд суммарно. Контейнер удаляется после запуска. Docker socket не передаётся."),node("p","",js ? "Node.js 24 и TypeScript 5.9.3. Vitest 4.0.18 / Vite 7.3.1 используют подготовленные зависимости и Node-окружение. Версии и lock проекта не воспроизводятся. Настройки проекта и npm scripts не запускаются; JSX/TSX, Jest, React и сборка приложения пока не поддерживаются." : "Python 3.12. Окружение «Данные» содержит Pydantic 2.14.1 и PyYAML 6.0.3. requirements.txt проекта не устанавливается; настройки инструментов игнорируются. conftest.py выполняется внутри контейнера."),node("p","","В снимок входят файлы выбранного языка и поддержанные форматы данных. Типичные секретные файлы, ссылки, зависимости и служебные папки исключены. Логи могут содержать фрагменты исходников и остаются локальными."));
  const environment=node("select");environment.setAttribute("aria-label",js ? "Окружение Node.js" : "Окружение Python");environment.id="runner-environment";
  for(const [value,title] of Object.entries(environmentLabels)) {if(value.startsWith("node-")!==js)continue;const option=node("option","",title);option.value=value;environment.append(option);}
  const environmentLabel=node("label","runner-environment",js ? "Среда для тестов JavaScript / TypeScript" : "Среда для тестов Python");environmentLabel.append(environment);body.append(environmentLabel);
  const compatibilityPanel=section("Совместимость проекта");if(js){compatibilityPanel.append(node("p","","Читаем package.json без запуска кода…"));body.append(compatibilityPanel);}
  let compatibility=null,submitted=false;
  const consent=node("input");consent.type="checkbox";consent.id=`run-consent-${project.id}`;
  const label=node("label","ai-consent");label.append(consent,document.createTextNode("Разрешаю выполнить код тестов этого проекта в контейнере Docker"));
  const run=node("button","primary-button runner-start","Запустить тесты");run.prepend(icon("play"));run.dataset.consentId=consent.id;run.dataset.compatible=js ? "false" : "true";run.disabled=true;
  const hint=node("p","check-note","Подтвердите запуск кода, чтобы продолжить.");hint.id="runner-start-hint";hint.setAttribute("role","status");run.setAttribute("aria-describedby",hint.id);
  const updateRunner=()=>{
    const allowed=environment.value==="node-vitest-v1" ? ["vitest","vite","typescript","@types/node"] : ["typescript","@types/node"];
    const expected=environment.value==="node-vitest-v1" ? "vitest" : "node";
    const compatible=!js || (compatibility && compatibility.status!=="unsupported" && compatibility.framework===expected && compatibility.dependencies.every(name=>allowed.includes(name)));
    run.dataset.compatible=compatible && !submitted ? "true" : "false";run.disabled=submitted || !compatible || !consent.checked || state.job?.state==="running";
    hint.textContent=submitted ? "Запрос отправлен. Автоматического повтора нет." : !compatible ? "Это окружение пока не подходит проекту. Посмотрите причину выше." : state.job?.state==="running" ? "Сейчас выполняется другая проверка. Дождитесь её завершения." : !consent.checked ? "Подтвердите запуск кода, чтобы продолжить." : "Всё готово. Проверка начнётся после нажатия кнопки.";
    if(js && compatibility){compatibilityPanel.replaceChildren(node("h3","","Совместимость проекта"));renderCompatibility(compatibilityPanel,compatibility);if(!compatible)compatibilityPanel.append(node("p","detail-callout","Выбранная среда не поддерживает эти зависимости. Отсутствующие пакеты автоматически не устанавливаются."));}
  };
  consent.addEventListener("change",updateRunner);environment.addEventListener("change",()=>{consent.checked=false;updateRunner();});
  if(js)environment.disabled=true;
  run.addEventListener("click",async()=>{
    submitted=true;consent.disabled=true;environment.disabled=true;updateRunner();
    try {
      const data=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:"project_runner",project_id:project.id,consent:environment.value==="node-vitest-v1" ? "javascript-vitest-v1" : js ? "javascript-checks-v1" : "python-checks-v1",environment:environment.value})});
      if(!data.job)throw new Error(data.error || "Запуск не подтверждён");renderJob(data.job);if(sequence===detailSequence && !$("drawer").hidden)closeDetail();
    } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden)body.append(node("p","detail-callout",`${problem.message} Запрос не повторяется. Откройте историю, чтобы проверить состояние.`));}
  });
  body.append(advanced,label,hint,run);
  const history=node("button","secondary-button","Посмотреть прошлые запуски");history.addEventListener("click",()=>projectResults(project,origin,"tests"));const footer=node("div","drawer-actions");footer.append(history);body.append(footer);
  if(js) {
    try {const data=await api(`/api/project-compatibility/${project.id}`);if(sequence!==detailSequence || $("drawer").hidden)return;compatibility=data;if(data.suggested_environment)environment.value=data.suggested_environment;environment.disabled=false;updateRunner();}
    catch(problem) {if(sequence!==detailSequence || $("drawer").hidden)return;compatibilityPanel.replaceChildren(node("p","detail-callout","Не удалось проверить package.json. Запуск недоступен; сохранённую историю можно читать."));hint.textContent="Не удалось определить совместимость проекта.";}
  } else updateRunner();
}
async function openRunReport(id,origin) {
  const sequence=openRunnerDrawer("Результаты проверки кода",origin), body=$("detail-body");
  try {
    const data=await api(`/api/project-run/${id}`); if(sequence!==detailSequence || $("drawer").hidden) return;
    const project=state.projects.find(item=>item.id===data.claim.project_id);
    if(project)drawerBack(body,"К результатам проекта",()=>projectResults(project,origin,"tests"));
    const result=data.result;
    body.append(node("p","detail-subtitle",formatTime(data.claim.created_at,true)),node("span",`status ${result?.status === "passed" ? "passed" : "issues"}`,runLabels[result?.status || "pending"]));
    pair(body,"Окружение",environmentLabels[data.claim.environment || "minimal"]);
    if(!result) {body.append(node("p","detail-callout","Подтверждённого результата пока нет. Дождитесь завершения. После прерывания контейнер мог остаться активным; автоматического повторного запуска нет.")); return;}
    const reasons={runner_unavailable:"Образ выбранного окружения не подготовлен или Docker Desktop недоступен.",project_incompatible:"Объявления проекта или настройки тестов не поддерживаются выбранным окружением. Код не запускался.",snapshot_rejected:"Не удалось подготовить снимок: нет файлов выбранного языка, достигнут лимит, папка недоступна или изменилась.",sandbox_failed:"Ошибка контейнера, недопустимый ответ или очистка не подтверждена. Успешным этот запуск не считается.",deadline:"Превышено общее время запуска. Проверьте подтверждение удаления контейнера.",uncertain:"Состояние запуска не подтверждено.",checks_completed:"Проверки завершены в рамках выбранного профиля. Это не подтверждает полную работоспособность или безопасность проекта."};
    body.append(node("p","detail-callout",reasons[result.reason]));
    if(result.reason === "runner_unavailable") body.append(node("p","detail-subtitle",`Подготовка окружения: .venv/Scripts/python.exe scripts/prepare-project-runner.py${data.claim.environment && data.claim.environment !== "minimal" ? ` --environment ${data.claim.environment}` : ""}`));
    if(result.compatibility) {const panel=section("Совместимость снимка");renderCompatibility(panel,result.compatibility);for(const issue of result.compatibility_issues || [])panel.append(node("p","detail-callout",compatibilityIssueLabels[issue] || "Окружение не поддерживает проект."));body.append(panel);}
    if(data.missing_modules?.length) {
      body.append(node("p","detail-callout",`Недоступны модули: ${data.missing_modules.join(", ")}. Это может быть внешняя зависимость или часть самого проекта. Имя модуля может отличаться от имени пакета.`));
      body.append(node("p","","Для pydantic и yaml выберите окружение «Данные». Другие зависимости пока не поддерживаются готовыми окружениями. Пакеты автоматически не устанавливаются; диагноз основан на выводе тестов."));
    }
    if(result.execution_scope==="not_started" && ["project_incompatible","snapshot_rejected","runner_unavailable"].includes(result.reason)) pair(body,"Контейнер","Не создавался");
    else pair(body,"Контейнер удалён",result.container_removed ? "Да" : "Не подтверждено");
    if(result.snapshot) {pair(body,"Файлов в снимке",result.snapshot.files);pair(body,"Пропущено файлов/папок",result.snapshot.skipped);pair(body,"SHA256 снимка",result.snapshot.sha256);}
    const labels={passed:"Пройдено",failed:"Найдены ошибки",error:"Ошибка инструмента или среды",timeout:"Время истекло",no_tests:"Тесты не найдены"};
    for(const check of result.checks) {
      const card=section(toolLabels[check.tool]);
      card.append(node("strong","",labels[check.status]));
      if(check.output_truncated) card.append(node("p","detail-callout","Лимит вывода достигнут. Проверка остановлена; полный результат не подтверждён."));
      const logs=node("details","runner-logs"); logs.append(node("summary","","Показать локальный вывод"),node("pre","",check.output || "Вывод отсутствует.")); card.append(logs); body.append(card);
    }
    body.append(node("p","detail-subtitle","Вывод получен из контейнера с кодом проекта и остаётся локальным. Он не является независимым доказательством корректности тестов."));
    pair(body,"ID запуска",data.claim.id);
  } catch(problem) {if(sequence===detailSequence) body.append(node("p","detail-callout","Результат запуска недоступен. Повтор автоматически не выполняется."));}
}

const reviewLabels={attention:"Требует внимания",incomplete:"Проверки неполные",no_issues_reported:"Замечаний в выбранных проверках нет"};
const reviewCategoryLabels={syntax:"Ошибка синтаксиса или формата",encoding:"Файл не удалось декодировать",read_failed:"Файл не удалось прочитать",yaml_alias:"Ссылки YAML вне области проверки",privileged:"Привилегированный контейнер",host_namespace:"Пространство имён хоста",mutable_image:"Незакреплённый образ",missing_limits:"Не указаны ограничения ресурсов",capabilities:"Расширенные полномочия контейнера",privilege_escalation:"Разрешено повышение привилегий",host_path:"Доступ к файловой системе хоста",docker_socket:"Доступ к Docker socket",limit:"Достигнут лимит проверки",runner_failed:"Инструмент сообщил об ошибках",runner_error:"Ошибка инструмента или среды",runner_timeout:"Превышено время проверки",runner_no_tests:"Тесты не найдены",runner_pending:"Результат запуска неизвестен",runner_not_supported:"Профиль не поддерживает проект",runner_unconfirmed:"Выполнение или очистка не подтверждены"};
const reviewReasonLabels={checks_completed:"Проверки завершены",runner_unavailable:"Среда проверки недоступна",snapshot_rejected:"Не удалось подготовить снимок файлов",project_incompatible:"Проект не поддерживается окружением",sandbox_failed:"Ошибка контейнера или очистки",deadline:"Превышено общее время запуска",uncertain:"Результат не подтверждён"};
const reviewLimitations={
  unverified_source_match:"Сохранённые проверки могли исследовать разные версии файлов. Совпадение исходников между ними и актуальность текущей папки не подтверждены.",
  missing_static:"Статический отчёт не выбран. Синтаксис и настройки по его правилам в этой сводке не оценены.",
  missing_runner:"Запуск тестов не выбран. Поведение кода в этой сводке не проверено.",
  pending_runner:"Подтверждённого результата выбранного запуска пока нет.",
  partial_static:"Статическая проверка частичная: часть файлов или конструкций не была проверена.",
  unconfirmed_runner:"Полнота выполнения проверок или удаление контейнера не подтверждены.",
  truncated_output:"Вывод инструмента достиг лимита; полный результат не подтверждён.",
  selected_profile_only:"Результаты относятся только к выбранным правилам и профилю тестирования. Они не подтверждают полную работоспособность или безопасность проекта.",
};
function reviewBadge(status) {return node("span",`status ${status==="no_issues_reported" ? "passed" : "issues"}`,reviewLabels[status] || "Результат неизвестен");}
async function projectReviews(project,origin) {
  const sequence=openRunnerDrawer(`Сводные отчёты · ${project.name}`,origin), body=$("detail-body");
  drawerBack(body,"К результатам проекта",()=>projectResults(project,origin,"reviews"));
  body.append(node("p","detail-callout","Соберите сводку из сохранённых результатов этого проекта. Это действие не запускает код, новые проверки или нейросеть."));
  body.append(node("p","",reviewLimitations.unverified_source_match));
  const loading=node("p","detail-subtitle","Загружаем сохранённые результаты…"); body.append(loading);
  const results=await Promise.allSettled([api(`/api/projects/${project.id}`),api(`/api/project-runs/${project.id}`),api(`/api/project-reviews/${project.id}`)]);
  if(sequence!==detailSequence || $("drawer").hidden) return;
  loading.remove();
  const [reportsData,runsData,historyData]=results;
  const reports=reportsData.status==="fulfilled" ? reportsData.value.reports : [];
  const runs=runsData.status==="fulfilled" ? runsData.value.runs : [];
  if(reportsData.status!=="fulfilled") body.append(node("p","detail-callout","История статических проверок недоступна. Их нельзя выбрать для новой сводки."));
  if(runsData.status!=="fulfilled") body.append(node("p","detail-callout","История тестов недоступна. Их нельзя выбрать для новой сводки."));
  const form=section("Создать сводный отчёт"), staticSelect=node("select"), runSelect=node("select");
  staticSelect.setAttribute("aria-label","Статическая проверка для сводного отчёта");runSelect.setAttribute("aria-label","Запуск тестов для сводного отчёта");
  const staticEmpty=node("option","","Без статической проверки"),runEmpty=node("option","","Без результатов тестов");
  staticEmpty.value="";runEmpty.value="";staticSelect.append(staticEmpty);runSelect.append(runEmpty);
  for(const report of reports) {
    const option=node("option","",`${formatTime(report.completed_at,true)} · ${statuses[report.status] || "Результат"} · ${report.id.slice(0,8)}`);option.value=report.id;staticSelect.append(option);
  }
  for(const item of runs) {
    const option=node("option","",`${formatTime(item.claim.created_at,true)} · ${runLabels[item.result?.status || "pending"]} · ${environmentLabels[item.claim.environment || "minimal"] || item.claim.profile} · ${item.claim.id.slice(0,8)}`);
    option.value=item.claim.id;option.disabled=!item.result;runSelect.append(option);
  }
  if(reports.length) staticSelect.value=reports[0].id;
  const completedRun=runs.find(item=>item.result);if(completedRun) runSelect.value=completedRun.claim.id;
  const staticLabel=node("label","","Статическая проверка"),runLabel=node("label","","Результаты тестов");staticLabel.append(staticSelect);runLabel.append(runSelect);
  const controls=node("div","comparison-controls"),create=node("button","primary-button","Собрать сводный отчёт");
  let submitting=false;
  const update=()=>{create.disabled=submitting || (!staticSelect.value && !runSelect.value);};
  staticSelect.addEventListener("change",update);runSelect.addEventListener("change",update);update();
  create.addEventListener("click",async()=>{
    submitting=true;update();staticSelect.disabled=true;runSelect.disabled=true;
    try {
      const data=await api("/api/project-reviews",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({project_id:project.id,static_report_id:staticSelect.value || null,run_id:runSelect.value || null})});
      if(!data.review?.id) throw new Error(data.error || "Не удалось подтвердить сохранение сводки.");
      if(sequence!==detailSequence || $("drawer").hidden) return;
      openReview(data.review.id,origin);
    } catch(problem) {
      if(sequence===detailSequence && !$("drawer").hidden) form.append(node("p","detail-callout",`${problem.message} Автоматического повтора нет. Откройте список сводных отчётов снова, чтобы проверить сохранение.`));
    }
  });
  controls.append(staticLabel,runLabel,create);form.append(controls);
  if(!reports.length && !completedRun) form.append(node("p","detail-subtitle","Для сводки нужна хотя бы одна сохранённая статическая проверка или завершённый запуск тестов."));
  if(runs.some(item=>!item.result)) form.append(node("p","detail-subtitle","Запуски без подтверждённого результата показаны в списке, но выбрать их нельзя."));
  form.append(node("p","detail-subtitle","Повторное сохранение того же набора результатов откроет существующую сводку и сохранит состояние её нейросетевого разбора."));body.append(form);
  const history=section("Сохранённые сводки");body.append(history);
  if(historyData.status!=="fulfilled") {history.append(node("p","detail-callout","Список сводок недоступен. Сохранённые результаты не изменялись."));return;}
  if(!historyData.value.reviews.length) history.append(node("p","","Сводных отчётов пока нет."));
  for(const review of historyData.value.reviews) {
    const row=node("div","runner-history-row"),open=node("button","text-button review-open","Открыть сводный отчёт");
    row.append(reviewBadge(review.status),node("time","",formatTime(review.created_at,true)));
    open.addEventListener("click",()=>openReview(review.id,origin));row.append(open);history.append(row);
  }
}
async function openReview(id,origin) {
  const sequence=openRunnerDrawer("Сводный отчёт проекта",origin),body=$("detail-body");
  try {
    const data=await api(`/api/project-review/${id}`);if(sequence!==detailSequence || $("drawer").hidden) return;
    const review=data.review,project=state.projects.find(item=>item.id===review.project_id);
    if(project)drawerBack(body,"К результатам проекта",()=>projectResults(project,origin,"reviews"));
    if(project) pair(body,"Проект",project.name);
    body.append(reviewBadge(review.status),node("p","detail-subtitle",formatTime(review.created_at,true)));
    body.append(node("p","detail-callout",reviewLimitations.unverified_source_match));
    body.append(node("p","","Это сводка выбранных исторических результатов. Отсутствие замечаний в них не означает, что проект безопасен или полностью работает."));
    const staticPanel=section("Статическая проверка");body.append(staticPanel);
    if(review.static) {
      staticPanel.append(statusBadge(review.static.status));
      pair(staticPanel,"Правила",review.static.rules_version);pair(staticPanel,"Файлов проверено",review.static.files_checked);pair(staticPanel,"Пропусков",review.static.skipped);
      if(review.static.partial) staticPanel.append(node("p","detail-callout",reviewLimitations.partial_static));
      for(const item of review.static.categories) staticPanel.append(node("p","",`${reviewCategoryLabels[item.code] || item.code} · ${item.severity==="error" ? "ошибка" : "замечание"} · ${item.count}`));
      if(!review.static.categories.length) staticPanel.append(node("p","","В выбранном статическом отчёте замечаний не обнаружено."));
      const open=node("button","text-button","Открыть исходный статический отчёт");open.addEventListener("click",()=>openProjectReport(review.static_report_id,origin));staticPanel.append(open);
    } else staticPanel.append(node("p","",reviewLimitations.missing_static));
    const runnerPanel=section("Проверки кода и тесты");body.append(runnerPanel);
    if(review.runner) {
      const runner=review.runner;
      pair(runnerPanel,"Состояние",runLabels[runner.state] || runner.state);pair(runnerPanel,"Профиль",runner.profile);pair(runnerPanel,"Окружение",environmentLabels[runner.environment] || runner.environment);
      pair(runnerPanel,"Исполнение",runner.execution_scope==="container" ? "В контейнере" : "Код не запускался");
      if(runner.execution_scope==="container") pair(runnerPanel,"Контейнер удалён",runner.container_removed ? "Да" : "Не подтверждено");
      pair(runnerPanel,"Причина завершения",reviewReasonLabels[runner.reason] || "Не подтверждена");
      const checkLabels={passed:"Пройдено",failed:"Найдены ошибки",error:"Ошибка инструмента или среды",timeout:"Время истекло",no_tests:"Тесты не найдены"};
      for(const check of runner.checks) {
        runnerPanel.append(node("p","",`${toolLabels[check.tool] || check.tool} · ${checkLabels[check.status] || check.status}`));
        if(check.output_truncated) runnerPanel.append(node("p","detail-callout",reviewLimitations.truncated_output));
      }
      for(const issue of runner.compatibility_issues || [])runnerPanel.append(node("p","detail-callout",compatibilityIssueLabels[issue] || issue));
      const open=node("button","text-button","Открыть исходный запуск и локальные логи");open.addEventListener("click",()=>openRunReport(review.run_id,origin));runnerPanel.append(open);
    } else runnerPanel.append(node("p","",reviewLimitations.missing_runner));
    const limits=section("Ограничения сводки");
    for(const limitation of review.limitations)limits.append(node("p","",reviewLimitations[limitation] || limitation));body.append(limits);
    body.append(reviewAiSection(data,sequence));pair(body,"ID сводного отчёта",review.id);
  } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden)body.append(node("p","detail-callout","Сводный отчёт недоступен. Новые проверки и запросы нейросети не выполнялись."));}
}
function reviewAiSection(data,sequence) {
  const panel=section("Нейросетевой разбор сводки"),analysis=data.analysis,review=data.review;panel.classList.add("project-ai-panel");
  panel.append(node("p","","Модель объясняет только сводку сохранённых проверок. Она не читает исходники или логи и не проверяет рекомендации исполнением."));
  pair(panel,"Провайдер / модель",`OpenRouter · ${data.model}`);
  if(analysis.status==="completed") {
    panel.append(node("span","status passed","Ответ модели получен"),node("p","ai-summary",analysis.response.summary));
    const priorities={high:"Высокий",medium:"Средний",low:"Низкий"};
    for(const action of analysis.response.actions) {
      const card=node("div","ai-action");card.append(node("strong","",`Приоритет: ${priorities[action.priority] || action.priority}`),node("small","finding-location",reviewCategoryLabels[action.code] || action.code),node("p","",action.explanation),node("strong","","Следующий шаг"),node("p","",action.next_step));panel.append(card);
    }
    const limitations=Array.isArray(analysis.response.limitations) ? analysis.response.limitations.join(" ") : analysis.response.limitations;
    panel.append(node("p","detail-callout",limitations),node("p","detail-subtitle","Это мнение модели по сводке. Рекомендации требуют ручной проверки; файлы и настройки не изменялись."));
  } else if(analysis.status==="pending") {
    panel.append(node("p","detail-callout","Разбор уже запрошен. Дождитесь завершения и откройте сводку снова. При прерывании приложения запрос мог быть оплачен; повтор не отправляется."));
  } else if(analysis.status==="failed") {
    panel.append(node("p","detail-callout","Ответ модели не подтверждён. Сводка сохранена; повторного запроса для этого набора результатов нет, поскольку попытка могла быть оплачена. Повторное создание такой же сводки не сбрасывает состояние разбора."));
  } else if(analysis.status==="not_requested") {
    panel.append(node("p","","В OpenRouter отправятся только количества, коды категорий, статусы инструментов и ограничения проверки. Исходники, логи, пути, названия проектов, UUID, хеши и имена пакетов не отправляются."));
    const preview=node("details","ai-preview");preview.append(node("summary","","Посмотреть отправляемую сводку"),node("pre","",JSON.stringify(data.payload,null,2)));panel.append(preview);
    panel.append(node("p","detail-subtitle","Один платный запрос, до 1600 выходных токенов. Используется отдельный ключ AegisOps. Для одного набора результатов — одна попытка без автоматического повтора."));
    const consent=node("input"),label=node("label","ai-consent");consent.type="checkbox";consent.id=`review-ai-consent-${review.id}`;
    label.append(consent,document.createTextNode("Отправить показанную сводку в OpenRouter и выполнить один платный разбор"));
    const run=node("button","primary-button project-ai-start","Разобрать сводку с нейросетью");run.dataset.consentId=consent.id;run.disabled=true;
    consent.addEventListener("change",()=>{run.disabled=!consent.checked || state.job?.state==="running";});
    run.addEventListener("click",async()=>{
      run.disabled=true;run.dataset.compatible="false";consent.disabled=true;
      try {
        const result=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:"project_review_ai",review_id:review.id,consent:data.ai_profile})});
        if(result.error || !result.job) throw new Error(result.error || "Состояние запроса не подтверждено.");
        renderJob(result.job);
        if(sequence===detailSequence && !$("drawer").hidden)closeDetail();
        toast("Состояние нейросетевого разбора показано над списком проектов.");
      } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden)panel.append(node("p","detail-callout",`${problem.message} Запрос не повторяется автоматически. Откройте сводку снова, чтобы проверить состояние.`));}
    });
    panel.append(label,run);
  } else panel.append(node("p","detail-callout","Состояние разбора неизвестно. Новый запрос не отправлен."));
  return panel;
}

function comparisonControls(items,kind,origin) {
  const controls=node("div","comparison-controls"), base=node("select"), target=node("select");
  base.setAttribute("aria-label","Исходный отчёт для сравнения"); target.setAttribute("aria-label","Новый отчёт для сравнения");
  const baseLabel=node("label","","Исходный отчёт"), targetLabel=node("label","","Сравнить с");
  items.forEach((item,index)=>{
    for(const select of [base,target]) {const option=node("option","",`${formatTime(item.time,true)} · ${item.id.slice(0,8)}${index===0 ? " · последний" : ""}`);option.value=item.id;select.append(option);}
  });
  base.value=items[1].id; target.value=items[0].id;
  const compare=node("button","text-button","Сравнить отчёты"); compare.append(icon("layers"));
  const update=()=>{compare.disabled=base.value===target.value;}; base.addEventListener("change",update);target.addEventListener("change",update);
  compare.addEventListener("click",()=>openComparison(kind,base.value,target.value,kind==="run" ? origin : compare));
  baseLabel.append(base);targetLabel.append(target);controls.append(baseLabel,targetLabel,compare);return controls;
}
async function openComparison(kind,base,target,origin) {
  const sequence=openRunnerDrawer(kind==="run" ? "Сравнение запусков кода" : "Сравнение статических отчётов",origin), body=$("detail-body");
  pair(body,"Исходный отчёт",base);pair(body,"Новый отчёт",target);
  try {
    const prefix=kind==="run" ? "run-comparison" : "project-comparison";
    const data=await api(`/api/${prefix}/${base}/${target}`);if(sequence!==detailSequence || $("drawer").hidden) return;
    if(!data.comparable) {
      const reasons={rules_changed:"Версии правил различаются. Появление и исчезновение замечаний нельзя оценить автоматически.",partial:"Один из отчётов частичный. Отсутствие замечания не означает, что оно устранено.",unfinished:"Один из запусков ещё не имеет подтверждённого результата.",unconfirmed:"Область проверки или результат одного из запусков не подтверждены полностью.",environment_changed:"Выбраны разные окружения. Смена зависимостей не считается исправлением кода.",image_changed:"Образы проверок различаются. Автоматическое сравнение отключено."};
      body.append(node("p","detail-callout",reasons[data.reason]));
      if(kind!=="run") {pair(body,"Версии правил",`${data.base_rules} → ${data.target_rules}`);pair(body,"Всего замечаний",`${data.base_total} → ${data.target_total}`);}
      return;
    }
    if(kind==="run") {
      body.append(node("p","detail-callout","Сравниваются статусы инструментов в одном образе. Это не сравнение отдельных тестов, количества ошибок или доказательство исправления кода."));
      pair(body,"Снимок файлов",data.same_snapshot ? "Совпадает" : "Изменился");
      const statuses={passed:"Пройдено",failed:"Найдены ошибки",error:"Ошибка инструмента или среды",timeout:"Время истекло",no_tests:"Тесты не найдены"};
      const changes={unchanged:"Статус не изменился",now_passed:"Теперь пройдено",no_longer_passed:"Больше не проходит",changed:"Статус изменился"};
      for(const item of data.transitions) {const card=section(toolLabels[item.tool]);card.append(node("strong","",changes[item.change]),node("p","",`${statuses[item.before]} → ${statuses[item.after]}`));body.append(card);}
      if(data.same_snapshot) body.append(node("p","detail-subtitle","Одинаковый снимок может давать разные результаты из-за поведения тестов. Порядок выполнения и окружение внутри контейнера не аттестованы."));
    } else {
      pair(body,"Версия правил",data.target_rules);
      const stats=node("div","report-stats");
      for(const [value,label] of [[data.added.length,"Появились"],[data.removed.length,"Больше не обнаружены"],[data.unchanged,"Остались"]]) {const cell=node("div","report-stat");cell.append(node("strong","",value),node("span","",label));stats.append(cell);}body.append(stats);
      body.append(node("p","detail-callout","Сравниваются замечания по файлу, коду и координатам при одинаковых правилах. Переименование файла или сдвиг строки могут выглядеть как исчезновение и появление. Это не доказательство исправления ошибки."));
      for(const [title,items] of [["Появились",data.added],["Больше не обнаружены",data.removed]]) {
        const group=section(`${title}: ${items.length}`);
        for(const item of items) {const card=node("div",`finding-card ${item.severity}`);card.append(node("strong","",`${item.file}${item.line ? ` · строка ${item.line}` : ""}`));if(item.location)card.append(node("code","finding-location",item.location));card.append(node("p","",item.message));group.append(card);}body.append(group);
      }
    }
    body.append(node("p","detail-subtitle","Сравнение использует сохранённые результаты. Новые проверки и запросы нейросети не запускаются."));
  } catch(problem) {if(sequence===detailSequence && !$("drawer").hidden)body.append(node("p","detail-callout","Не удалось сравнить отчёты. Проверьте выбор и доступность истории."));}
}

showView("projects");
refresh();
