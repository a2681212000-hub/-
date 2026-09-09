const $ = (id) => document.getElementById(id);
let runs = Number(localStorage.getItem('office-agent-runs') || 0);
let history = JSON.parse(localStorage.getItem('office-agent-history') || '[]');
const aiSettings = JSON.parse(localStorage.getItem('office-agent-ai') || '{}');
$('runCount').textContent = runs;
$('aiBaseUrl').value = aiSettings.base_url || '';
$('aiModel').value = aiSettings.model || '';

function renderHistory() {
  const el = $('history');
  if (!history.length) { el.innerHTML = '<div class="empty">还没有执行记录</div>'; return; }
  el.innerHTML = history.slice(0, 6).map(item => `<div class="history-row"><strong>${item.task}</strong><span>${item.time}</span><span>${item.rows} 行</span><span>${item.output}</span></div>`).join('');
}
renderHistory();

const toolLabels = {list_files:'扫描输入文件', process_report:'处理并生成报告', list_reports:'查看最近报告'};
function renderPlan(plan) {
  const steps = plan && Array.isArray(plan.steps) ? plan.steps : [];
  $('planBox').hidden = !steps.length;
  $('planSteps').innerHTML = steps.map((step, index) => `<span class="plan-step"><b>${index + 1}</b>${toolLabels[step.tool] || step.tool}</span>`).join('');
}

$('runBtn').addEventListener('click', () => {
  const btn = $('runBtn');
  btn.disabled = true; btn.innerHTML = '<span>…</span> 处理中';
  $('resultTitle').textContent = '正在处理'; $('resultIcon').textContent = '…';
  $('log').className = 'log'; $('log').innerHTML = '<span class="log-dot"></span><span>正在读取数据并检查字段…</span>';
  const ai = {base_url:$('aiBaseUrl').value.trim(), model:$('aiModel').value.trim(), api_key:$('aiKey').value.trim()};
  fetch('/api/task', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({task:$('task').value, folder:$('folder').value, ai})})
  .then(response => response.json())
  .then(data => {
    if (!data.ok) throw new Error(data.error || '任务执行失败');
    renderPlan(data.plan);
    runs += 1; $('runCount').textContent = runs; localStorage.setItem('office-agent-runs', runs);
    const now = new Date(); const time = now.toLocaleString('zh-CN', {hour12:false});
    const outputMatch = data.message.match(/报告：(.+)/); const output = outputMatch ? outputMatch[1].trim() : '已生成';
    const rowsMatch = data.message.match(/输出 (\d+) 行/); const rowCount = rowsMatch ? rowsMatch[1] : '—';
    $('resultTitle').textContent = '处理完成'; $('resultIcon').textContent = '✓';
    $('files').textContent = String((data.message.match(/读取 (\d+) 个文件/) || [,'—'])[1]); $('rows').textContent = rowCount; $('issues').textContent = data.message.includes('异常：') ? '有' : '0'; $('output').textContent = output;
    $('log').className = 'log success'; $('log').innerHTML = '<span class="log-dot"></span><span>校验通过，报告已经生成。</span>';
    history.unshift({task:$('task').value || '报表处理',time,rows:rowCount,output}); history = history.slice(0,6); localStorage.setItem('office-agent-history', JSON.stringify(history)); renderHistory();
  })
  .catch(error => { $('resultTitle').textContent = '执行失败'; $('resultIcon').textContent = '!'; $('log').className = 'log error'; $('log').innerHTML = `<span class="log-dot"></span><span>${error.message}</span>`; })
  .finally(() => {
    btn.disabled = false; btn.innerHTML = '<span>▶</span> 开始处理';
  });
});
$('clearHistory').addEventListener('click', () => { history=[]; localStorage.removeItem('office-agent-history'); renderHistory(); });
$('pickFolder').addEventListener('click', () => $('folder').focus());
$('openReport').addEventListener('click', () => { if ($('output').textContent !== '尚未生成') alert('报告路径：' + $('output').textContent); });
$('settingsBtn').addEventListener('click', () => { $('settingsOverlay').hidden = false; });
$('closeSettings').addEventListener('click', () => { $('settingsOverlay').hidden = true; });
$('settingsOverlay').addEventListener('click', (event) => { if (event.target === $('settingsOverlay')) $('settingsOverlay').hidden = true; });
$('saveSettings').addEventListener('click', () => {
  localStorage.setItem('office-agent-ai', JSON.stringify({base_url:$('aiBaseUrl').value.trim(), model:$('aiModel').value.trim()}));
  $('settingsNote').textContent = '设置已保存，密钥只在本次页面运行时使用.';
  setTimeout(() => {$('settingsOverlay').hidden = true; $('settingsNote').textContent = '';}, 900);
});
$('clearLocal').addEventListener('click', () => {
  if (!confirm('清除浏览器中的执行记录和当前结果？不会删除 inbox 或报告文件。')) return;
  runs = 0; history = []; localStorage.removeItem('office-agent-runs'); localStorage.removeItem('office-agent-history');
  $('runCount').textContent = '0'; $('files').textContent = '—'; $('rows').textContent = '—'; $('issues').textContent = '—'; $('output').textContent = '尚未生成'; $('resultTitle').textContent = '等待任务'; $('resultIcon').textContent = '—'; renderHistory(); $('settingsNote').textContent = '本地记录已清除。';
});
