const form = document.querySelector('.score-form');
const live = document.querySelector('[data-judge-sync]');
let dirty = form?.dataset.scoreDirty === 'true';
let submitting = false;

if (form) {
  const inputs = [...form.querySelectorAll('[data-weight]')];
  const updateTotal = () => {
    const total = inputs.reduce((sum, input) => {
      const value = Math.min(10, Math.max(0, Number(input.value) || 0));
      return sum + value * Number(input.dataset.weight) / 10;
    }, 0);
    form.querySelector('[data-score-total]').value = total.toFixed(2);
  };
  form.addEventListener('input', () => {
    dirty = true;
    form.querySelector('[data-score-status]').textContent = '有未保存的修改；现场换队不会覆盖当前输入。';
    updateTotal();
  });
  form.addEventListener('submit', event => {
    queueMicrotask(() => {
      if (!event.defaultPrevented) {
        submitting = true;
        dirty = false;
      }
    });
  });
  window.addEventListener('beforeunload', event => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('pageshow', event => {
    submitting = false;
    if (event.persisted) {
      dirty = true;
      form.querySelector('[data-score-status]').textContent = '从历史记录恢复；请核对已保存的评分后再提交。';
    }
  });
  updateTotal();
}

if (live) {
  const status = live.querySelector('[data-live-status]');
  const team = live.querySelector('[data-live-team]');
  const follow = live.dataset.follow === 'true';
  const poll = async () => {
    try {
      const response = await fetch(live.dataset.stateUrl, {
        cache: 'no-store', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(5000),
      });
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) {
        throw new Error(`Judging state returned HTTP ${response.status}`);
      }
      const data = await response.json();
      if (typeof data.open !== 'boolean' || !Number.isInteger(data.scoring_revision) ||
          (data.current !== null && (typeof data.current?.id !== 'string' || typeof data.current?.can_score !== 'boolean'))) {
        throw new TypeError('Invalid judging state response');
      }
      const current = data.current;
      team.textContent = current ? `${current.team} · ${current.title}` : '等待下一队开始路演';
      status.classList.remove('warning-text');
      if (form) form.querySelector('[type=submit]').disabled = !data.open;
      if (data.scoring_revision !== Number(live.dataset.rulesRevision)) {
        status.textContent = '算分规则已更新，请重新打开页面核对；当前输入尚未保存。';
        if (form) form.querySelector('[type=submit]').disabled = true;
        if (!dirty && !submitting) window.location.reload();
        return;
      }
      if (current && !current.can_score) {
        status.textContent = '当前队伍需回避或尚未通过材料审核；可继续回看其他作品。';
      } else if (current && current.id !== live.dataset.projectId) {
        if (follow && !dirty && !submitting && current.score_url) {
          const target = new URL(current.score_url, window.location.origin);
          if (target.origin !== window.location.origin) throw new TypeError('Invalid scoring URL');
          window.location.replace(target.href);
          return;
        }
        status.textContent = dirty
          ? '现场已换队。请先保存当前队伍的评分，再返回当前路演；输入不会被覆盖。'
          : '回看模式 · 可随时返回当前路演。';
      } else {
        status.textContent = !data.open ? '评分已关闭，当前仅可回看。' : dirty
          ? '有未保存的修改 · 已暂停自动跟随。'
          : follow ? '已与现场同步 · 每 3 秒更新。' : '回看模式 · 不会自动切换队伍。';
      }
    } catch (error) {
      console.error('Could not synchronize live judging', error);
      status.textContent = '现场同步中断，正在重试；当前评分内容保留。若已退出登录，请重新登录。';
      status.classList.add('warning-text');
    } finally {
      window.setTimeout(poll, 3000);
    }
  };
  poll();
}
