const $ = id => document.getElementById(id);
const node = (tag, text, className) => Object.assign(document.createElement(tag), {textContent: text || '', className: className || ''});
function stored(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (_) { return fallback; } }
let history = stored('office-agent-history', []);
if (!Array.isArray(history)) history = [];
const aiSettings = stored('office-agent-ai', {});
$('aiBaseUrl').value = aiSettings.base_url || '';
$('aiModel').value = aiSettings.model || '';
let jobs = [];
let activeJob = null;
let busy = false;
let pollTimer = null;
const toolLabels = {list_files:'扫描输入文件', process_report:'处理并生成报告', list_reports:'查看最近报告', extract_pdf:'提取 PDF 信息', collect_web:'采集网页数据', list_mail_attachments:'扫描邮件附件', archive_files:'归档文件', notify:'生成通知草稿'};
const statusLabels = {running:'正在执行', pending:'待执行', approved:'已确认', awaiting_confirmation:'等待确认', completed:'已完成', cancelled:'已取消', expired:'确认已过期', failed:'执行失败', interrupted:'执行中断', skipped:'未执行'};
const dateText = value => new Date(value).toLocaleString('zh-CN', {hour12:false});

async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || '请求失败 (' + response.status + ')');
  return data;
}
function setLog(kind, message) {
  $('log').className = 'log ' + (kind || '');
  $('log').replaceChildren(node('span', '', 'log-dot'), node('span', message));
}
function controls() {
  const pending = activeJob && activeJob.status === 'awaiting_confirmation';
  $('runBtn').disabled = busy;
  $('runBtn').textContent = busy ? '处理中' : '开始处理';
  $('approveApproval').disabled = busy || !pending;
  $('cancelApproval').disabled = busy || !pending;
}
function renderHistory() {
  $('history').replaceChildren();
  if (!history.length) $('history').append(node('div', '还没有浏览记录', 'empty'));
  history.slice(0, 6).forEach(item => {
    const row = node('div', '', 'history-row');
    row.append(node('strong', item.task), node('span', item.time), node('span', item.status || ''), node('span', item.output));
    $('history').append(row);
  });
}
function remember(job) {
  if (job.status === 'running' || job.status === 'awaiting_confirmation') return;
  history = history.filter(item => item.jobId !== job.id);
  history.unshift({jobId:job.id, task:job.task, time:dateText(job.updated_at), status:statusLabels[job.status], output:job.message});
  history = history.slice(0, 6);
  localStorage.setItem('office-agent-history', JSON.stringify(history));
  renderHistory();
}
function renderJobs() {
  $('jobsList').replaceChildren();
  $('runCount').textContent = jobs.filter(job => job.status === 'completed').length;
  $('taskCount').textContent = jobs.filter(job => job.status === 'awaiting_confirmation').length + ' 个待确认';
  if (!jobs.length) $('jobsList').append(node('div', '暂无任务', 'empty'));
  jobs.forEach(job => {
    const row = node('div', '', 'job-row' + (activeJob && job.id === activeJob.id ? ' selected' : ''));
    const button = node('button', job.task, 'job-link');
    button.disabled = busy;
    button.onclick = () => selectJob(job.id);
    row.append(button, node('span', statusLabels[job.status] || job.status, 'job-status ' + job.status), node('time', dateText(job.created_at)));
    $('jobsList').append(row);
  });
}
function renderJob(job) {
  activeJob = job;
  sessionStorage.setItem('office-agent-active-job', job.id);
  $('resultTitle').textContent = statusLabels[job.status] || job.status;
  $('resultIcon').textContent = job.status === 'awaiting_confirmation' ? '?' : job.status === 'completed' ? '✓' : '!';
  const results = Array.isArray(job.results) ? job.results : [];
  const report = results.slice().reverse().find(result => result.tool === 'process_report');
  const files = results.find(result => result.tool === 'list_files');
  $('files').textContent = files ? String(files.count) : report && report.files ? String(report.files.length) : '—';
  $('rows').textContent = report ? String(report.rows || '—') : '—';
  $('issues').textContent = report ? String((report.problems || []).length) : '—';
  $('output').textContent = report && report.output ? report.output : '尚未生成';
  $('openReport').disabled = !(report && report.output);
  setLog(job.status === 'failed' || job.status === 'interrupted' ? 'error' : job.status === 'awaiting_confirmation' ? 'warning' : job.status === 'completed' ? 'success' : '', job.message);
  $('planBox').hidden = false;
  $('planSteps').replaceChildren();
  (job.steps || []).forEach((step, index) => {
    const item = node('div', '', 'plan-step');
    item.append(node('b', String(index + 1)), node('span', toolLabels[step.tool] || step.tool), node('span', statusLabels[step.status] || step.status, 'job-status ' + step.status));
    $('planSteps').append(item);
  });
  const pending = job.status === 'awaiting_confirmation';
  $('approvalBox').hidden = !pending;
  $('approvalFiles').replaceChildren();
  if (pending) {
    const preview = job.confirmation;
    $('approvalTitle').textContent = '归档 ' + preview.count + ' 个文件';
    $('approvalDetail').textContent = '来源：' + preview.folder + '\n目标：' + preview.target;
    $('approvalExpiry').textContent = '确认截止：' + dateText(preview.expires_at * 1000);
    preview.files.forEach(file => {
      const item = node('li', '');
      item.append(node('code', file.source), node('span', '移动至'), node('code', file.destination), node('small', file.size + ' 字节'));
      $('approvalFiles').append(item);
    });
  }
  $('taskDetails').hidden = false;
  $('taskEvents').replaceChildren(...(job.events || []).map(event => node('li', dateText(event.at) + '  ' + event.message)));
  $('taskResults').textContent = JSON.stringify(results, null, 2);
  controls();
  renderJobs();
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (activeJob && ['running', 'awaiting_confirmation'].includes(activeJob.status)) {
    pollTimer = setTimeout(async () => {
      try { const data = await api('/api/task/' + activeJob.id); renderJob(data.job); await loadJobs(false); }
      catch (error) { setLog('error', error.message); }
      schedulePoll();
    }, 3000);
  }
}
async function loadJobs(restore) {
  const data = await api('/api/tasks');
  jobs = data.tasks;
  $('serviceStatus').innerHTML = '<i></i> 本地服务已连接';
  if (restore && !activeJob) {
    const saved = sessionStorage.getItem('office-agent-active-job');
    const job = jobs.find(item => item.status === 'awaiting_confirmation') || jobs.find(item => item.id === saved);
    if (job) renderJob(job);
  }
  renderJobs();
  schedulePoll();
}
async function selectJob(id) {
  if (busy) return;
  try { const data = await api('/api/task/' + id); renderJob(data.job); remember(data.job); schedulePoll(); }
  catch (error) { setLog('error', error.message); }
}
async function openPath(path, mode, successMessage) {
  if (!path || path === '尚未生成') return;
  try { await api('/api/open-path', {path:path, mode:mode}); setLog('success', successMessage); }
  catch (error) { setLog('error', error.message); }
}
async function decideApproval(decision) {
  if (busy || !activeJob || activeJob.status !== 'awaiting_confirmation') return;
  busy = true; controls();
  try { const data = await api('/api/task/' + activeJob.id + '/decision', {confirmation_id:activeJob.confirmation.id, decision:decision}); renderJob(data.job); remember(data.job); }
  catch (error) { setLog('error', error.message); }
  finally { busy = false; controls(); await loadJobs(false).catch(error => setLog('error', error.message)); }
}
$('runBtn').onclick = async () => {
  if (busy) return;
  busy = true; controls(); $('approvalBox').hidden = true; $('resultTitle').textContent = '正在规划与执行'; setLog('', '任务已提交');
  const ai = {base_url:$('aiBaseUrl').value.trim(), model:$('aiModel').value.trim(), api_key:$('aiKey').value.trim()};
  try { const data = await api('/api/task', {task:$('task').value, folder:$('folder').value, ai:ai}); renderJob(data.job); remember(data.job); }
  catch (error) { $('resultTitle').textContent = '请求失败'; setLog('error', error.message); }
  finally { busy = false; controls(); await loadJobs(false).catch(error => setLog('error', error.message)); }
};
$('pickFolder').onclick = () => openPath($('folder').value.trim(), 'folder', '已打开数据来源文件夹');
$('approveApproval').onclick = () => decideApproval('approve');
$('cancelApproval').onclick = () => decideApproval('cancel');
$('clearHistory').onclick = () => { history = []; localStorage.removeItem('office-agent-history'); renderHistory(); };
$('openReport').onclick = () => { if (!$('openReport').disabled) openPath($('output').textContent, 'reveal', '已打开报告所在文件夹'); };
$('settingsBtn').onclick = () => { $('settingsOverlay').hidden = false; $('aiBaseUrl').focus(); };
$('closeSettings').onclick = () => { $('settingsOverlay').hidden = true; $('settingsBtn').focus(); };
$('settingsOverlay').onclick = event => { if (event.target === $('settingsOverlay')) $('settingsOverlay').hidden = true; };
document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('settingsOverlay').hidden) $('settingsOverlay').hidden = true; });
$('saveSettings').onclick = () => { localStorage.setItem('office-agent-ai', JSON.stringify({base_url:$('aiBaseUrl').value.trim(), model:$('aiModel').value.trim()})); $('settingsNote').textContent = '设置已保存'; };
$('clearLocal').onclick = () => { history = []; localStorage.removeItem('office-agent-history'); localStorage.removeItem('office-agent-runs'); renderHistory(); $('settingsNote').textContent = '浏览记录已清除，任务与确认记录保留'; };
renderHistory();
controls();
loadJobs(true).catch(error => { $('serviceStatus').innerHTML = '<i></i> 服务未连接'; setLog('error', error.message); });
