'use strict';
const $ = id => document.getElementById(id);
const state = {
  token: '',
  me: null,
  run: null,
  timer: null,
  pending: null
};
const labels = {
  active: '使用中',
  withdrawn: '已撤回',
  queued: '等待处理',
  running: '处理中',
  needs_input: '待补充信息',
  awaiting_review: '待审核',
  approved: '已批准',
  committed: '已提交',
  cancelled: '已取消',
  failed: '处理失败',
  rejected: '审核退回',
  abstained: '资料不足'
};
const errors = {
  citation_stale: '引用已更新或撤回，请创建新任务重新检索。',
  evidence_not_found: '原文当前已不可用，无法据此批准新答复。',
  approval_expired: '批准已过期，请审核人重新核对并批准。',
  stale_evidence: '引用已更新或撤回，请创建新任务重新检索。',
  unauthorized: '凭据无效或已被撤回。',
  role_forbidden: '当前身份没有此操作权限。',
  rate_limited: '请求过于频繁，请稍后再试。',
  capacity_exceeded: '处理队列已满，请稍后重试。',
  queue_full: '处理队列已满，请稍后重试。',
  revision_conflict: '资料版本已变化，请刷新后重试。',
  version_conflict: '任务已更新，请刷新后重新核对。',
  stale_version: '任务已更新，请刷新后重新核对。',
  evidence_stale: '引用资料已更新或撤回，请重新处理。',
  invalid_citation: '引用未通过版本或原文检查。',
  self_review_forbidden: '请由另一位审核人核对答复。',
  writes_disabled: '当前工作区暂停写入。',
  local_model_not_configured: '管理员尚未配置本地模型。'
};

function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function notify(text, error = false) {
  $('message').textContent = text;
  $('message').className = error ? 'error' : '';
  $('message').hidden = false;
  clearTimeout(state.timer);
  state.timer = setTimeout(() => $('message').hidden = true, 6500);
}
async function api(path, method = 'GET', body) {
  const token = state.token;
  const options = {
    method,
    headers: {
      Authorization: 'Bearer ' + state.token
    }
  };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch('/api' + path, options);
  } catch (e) {
    throw new Error('连接中断；操作可能已经生效，请先刷新任务状态。');
  }
  const data = await response.json();
  if (token !== state.token) throw new Error('连接身份已改变，请刷新当前工作区。');
  if (!response.ok) throw new Error(errors[data.error] || ('操作未完成：' + (data.error || response.status)));
  return data;
}
async function attempt(fn) {
  try {
    return await fn();
  } catch (e) {
    notify(e.message, true);
    return null;
  }
}

function key() {
  return crypto.randomUUID().replaceAll('-', '');
}

function badge(status) {
  return node('span', labels[status] || status, 'badge ' + status);
}

function date(value) {
  if (!value) return '';
  const d = new Date(typeof value === 'number' ? value * 1000 : value);
  return isNaN(d) ? '' : d.toLocaleString();
}

function action(text, callback, cls = '') {
  const b = node('button', text, cls);
  b.type = 'button';
  b.onclick = () => attempt(async () => {
    b.disabled = true;
    try {
      await callback();
    } finally {
      b.disabled = false;
    }
  });
  return b;
}
async function refresh() {
  await Promise.all([loadRuns(), loadDocuments(), loadMetrics()]);
  if (state.run) await openRun(state.run.id);
}
$('connect-form').onsubmit = e => {
  e.preventDefault();
  attempt(async () => {
    state.token = $('credential').value.trim();
    state.me = await api('/me');
    $('credential').value = '';
    $('identity').textContent = (state.me.role === 'reviewer' ? '审核人' : '申请人') + ' · ' + state.me.tenant;
    $('login').hidden = true;
    $('workspace').hidden = false;
    $('disconnect').hidden = false;
    $('task-form').hidden = state.me.role !== 'requester';
    $('document-form').hidden = state.me.role !== 'reviewer';
    $('mode').querySelector('[value="local"]').disabled = !state.me.local_model_enabled;
    await refresh();
  });
};
$('disconnect').onclick = () => {
  state.token = '';
  state.me = null;
  state.run = null;
  state.pending = null;
  $('workspace').hidden = true;
  $('login').hidden = false;
  $('disconnect').hidden = true;
  $('identity').textContent = '尚未连接';
  $('detail').replaceChildren(node('p', '选择一个任务开始'));
  $('run-list').replaceChildren();
  $('document-list').replaceChildren();
  $('metric-cards').replaceChildren();
  $('metrics-json').textContent = '';
  $('question').value = '';
  $('document-form').reset();
};
$('refresh').onclick = () => attempt(refresh);
for (const b of document.querySelectorAll('[data-tab]')) b.onclick = () => {
  for (const t of document.querySelectorAll('.tab')) t.hidden = t.id !== b.dataset.tab;
  for (const n of document.querySelectorAll('[data-tab]')) n.classList.toggle('selected', n === b);
};
$('task-form').onsubmit = e => {
  e.preventDefault();
  attempt(async () => {
    const question = $('question').value.trim(),
      mode = $('mode').value;
    const signature = JSON.stringify({
      question,
      mode
    });
    if (!state.pending || state.pending.signature !== signature) state.pending = {
      signature,
      key: key()
    };
    const run = await api('/runs', 'POST', {
      question,
      mode,
      idempotency_key: state.pending.key
    });
    state.pending = null;
    await loadRuns();
    await openRun(run.id);
    notify('任务已创建。可刷新查看进展。');
  });
};
async function loadRuns() {
  const data = await api('/runs');
  const list = $('run-list');
  list.replaceChildren();
  $('run-count').textContent = data.runs.length + ' 项';
  if (!data.runs.length) list.append(node('p', '尚无任务。', 'muted'));
  for (const run of data.runs) {
    const b = action('', () => openRun(run.id), 'run-item' + (state.run?.id === run.id ? ' active' : ''));
    b.append(badge(run.state), node('p', run.question));
    list.append(b);
  }
}
async function openRun(id) {
  const run = await api('/runs/' + encodeURIComponent(id));
  state.run = run;
  const box = $('detail');
  box.replaceChildren();
  const head = node('div', undefined, 'detail-head');
  head.append(badge(run.state), node('small', date(run.updated_at)));
  box.append(head, node('div', run.question, 'question'));
  if (run.clarification) {
    box.append(node('p', run.clarification, 'notice'));
    if (state.me.role === 'requester' && run.state === 'needs_input') {
      const form = node('form');
      const input = node('textarea');
      input.required = true;
      input.maxLength = 2000;
      input.rows = 3;
      input.setAttribute('aria-label', '补充信息');
      const b = node('button', '补充并继续', 'primary');
      form.append(input, b);
      form.onsubmit = e => {
        e.preventDefault();
        attempt(async () => {
          await api('/runs/' + id + '/reply', 'POST', {
            text: input.value,
            expected_version: run.version
          });
          await openRun(id);
          await loadRuns();
        });
      };
      box.append(form);
    }
  }
  if (run.error) box.append(node('p', '处理未完成：' + run.error, 'notice'));
  if (run.answer) box.append(node('h2', '答复草稿'), node('div', run.answer, 'answer'));
  if (run.citations?.length) {
    box.append(node('h2', '引用依据'));
    for (const c of run.citations) {
      const meta = run.observations.filter(o => o.name === 'search_knowledge').flatMap(o => o.result).find(e => e.id === c.id);
      const card = node('div', undefined, 'evidence');
      card.append(node('strong', meta?.title || '引用片段'), node('blockquote', c.quote), node('small', '版本 ' + c.revision.slice(0, 12) + '…'));
      card.append(action('查看原文片段', async () => {
        const source = await api('/evidence/' + encodeURIComponent(c.id));
        const full = node('div', undefined, 'source-context');
        full.append(node('strong', source.title), node('p', source.source, 'muted'), node('pre', source.text));
        card.querySelector('.source-context')?.remove();
        card.append(full);
      }));
      box.append(card);
    }
  }
  if (['awaiting_review', 'approved', 'rejected'].includes(run.state) && state.me.role === 'reviewer') {
    box.append(node('p', '逐项核对事实、适用条件和引用。可修订答复；批准只针对当前版本。', 'notice'));
    const form = node('form');
    const answer = node('textarea');
    answer.rows = 7;
    answer.required = true;
    answer.maxLength = 6000;
    answer.value = run.answer || '';
    answer.setAttribute('aria-label', '修订后的答复');
    const comment = node('input');
    comment.maxLength = 1000;
    comment.placeholder = '审核说明（可选）';
    const buttons = node('div', undefined, 'actions');
    for (const [decision, label, cls] of [
        ['approve', '核对无误，批准', 'primary'],
        ['reject', '退回处理', 'danger']
      ]) buttons.append(action(label, async () => {
      await api('/runs/' + id + '/review', 'POST', {
        expected_version: run.version,
        answer: answer.value,
        citations: run.citations.map(c => ({
          id: c.id,
          revision: c.revision,
          quote: c.quote
        })),
        decision,
        comment: comment.value
      });
      await openRun(id);
      await loadRuns();
    }, cls));
    form.append(answer, comment, buttons);
    form.onsubmit = e => e.preventDefault();
    box.append(form);
  }
  const actions = node('div', undefined, 'actions');
  if (state.me.role === 'requester' && ['queued', 'running', 'needs_input', 'awaiting_review', 'approved'].includes(run.state)) actions.append(action('取消任务', async () => {
    await api('/runs/' + id + '/cancel', 'POST');
    await openRun(id);
    await loadRuns();
  }, 'danger'));
  if (state.me.role === 'requester' && run.state === 'approved') actions.append(action('提交工单', async () => {
    await api('/runs/' + id + '/commit', 'POST', {
      expected_version: run.version,
      idempotency_key: 'commit_' + id
    });
    await openRun(id);
    await loadRuns();
    notify('工单已提交。重复提交会返回同一结果。');
  }, 'primary'));
  actions.append(action('刷新状态', async () => {
    await openRun(id);
    await loadRuns();
  }));
  box.append(actions);
  if (run.ticket_id) box.append(node('p', '工单编号：' + run.ticket_id, 'notice'));
  const details = node('details');
  details.append(node('summary', '处理记录'));
  const timeline = node('div');
  details.append(timeline);
  let loaded = false;
  details.ontoggle = () => {
    if (details.open && !loaded) attempt(async () => {
      const result = await api('/runs/' + id + '/events');
      for (const event of result.events) {
        const row = node('div', undefined, 'evidence');
        row.append(node('strong', event.phase || event.type || event.event || '记录'), node('pre', JSON.stringify(event, null, 2)));
        timeline.append(row);
      }
      loaded = true;
    });
  };
  box.append(details);
}
async function loadDocuments() {
  const result = await api('/documents');
  $('document-count').textContent = result.documents.length + ' 份';
  const list = $('document-list');
  list.replaceChildren();
  for (const doc of result.documents) {
    const card = node('div', undefined, 'card');
    card.append(node('h3', doc.title || doc.document_id), node('p', doc.source || '工作区资料', 'muted'), node('p', '版本 ' + doc.revision, 'small'));
    card.append(badge(doc.active ? 'active' : 'withdrawn'));
    if (state.me.role === 'reviewer') {
      card.append(action('准备更新', () => {
        const form = $('document-form');
        for (const name of ['document_id', 'title', 'source', 'license']) form.elements[name].value = doc[name] || '';
        form.elements.expected_revision.value = doc.revision;
        form.elements.content.value = '';
        form.scrollIntoView({
          behavior: 'smooth'
        });
        notify('请填写完整的新正文后发布。');
      }));
      if (doc.active) card.append(action('撤回资料', async () => {
        if (!confirm('撤回后，新审核与提交将不能继续引用这份资料。确认撤回？')) return;
        await api('/documents/' + encodeURIComponent(doc.document_id) + '/withdraw', 'POST', {
          expected_revision: doc.revision
        });
        await loadDocuments();
      }, 'danger'));
    }
    list.append(card);
  }
}
$('document-form').onsubmit = e => {
  e.preventDefault();
  attempt(async () => {
    const body = Object.fromEntries(new FormData(e.target));
    body.expected_revision = body.expected_revision || null;
    await api('/documents', 'POST', body);
    e.target.reset();
    await loadDocuments();
    notify('资料已发布，后续检索使用新版本。');
  });
};
async function loadMetrics() {
  const result = await api('/metrics');
  $('metrics-json').textContent = JSON.stringify(result, null, 2);
  const list = $('metric-cards');
  list.replaceChildren();
  const counts = result.states || result.state_counts || result.runs_by_state || {};
  for (const stateName of ['awaiting_review', 'committed', 'failed', 'cancelled']) {
    const card = node('div', undefined, 'card');
    card.append(node('p', labels[stateName], 'muted'), node('p', String(counts[stateName] || 0), 'metric-number'));
    list.append(card);
  }
}
