const $ = (id) => document.getElementById(id);
let runs = Number(localStorage.getItem('office-agent-runs') || 0);
let history = JSON.parse(localStorage.getItem('office-agent-history') || '[]');
$('runCount').textContent = runs;

function renderHistory() {
  const el = $('history');
  if (!history.length) { el.innerHTML = '<div class="empty">还没有执行记录</div>'; return; }
  el.innerHTML = history.slice(0, 6).map(item => `<div class="history-row"><strong>${item.task}</strong><span>${item.time}</span><span>${item.rows} 行</span><span>${item.output}</span></div>`).join('');
}
renderHistory();

$('runBtn').addEventListener('click', () => {
  const btn = $('runBtn');
  btn.disabled = true; btn.innerHTML = '<span>…</span> 处理中';
  $('resultTitle').textContent = '正在处理'; $('resultIcon').textContent = '…';
  $('log').className = 'log'; $('log').innerHTML = '<span class="log-dot"></span><span>正在读取数据并检查字段…</span>';
  fetch('/api/task', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({task:$('task').value, folder:$('folder').value})})
  .then(response => response.json())
  .then(data => {
    if (!data.ok) throw new Error(data.error || '任务执行失败');
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
