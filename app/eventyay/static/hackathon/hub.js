document.querySelectorAll('[data-confirm]').forEach((button) => {
  button.addEventListener('click', (event) => {
    if (!window.confirm(button.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = '已复制 ✓';
    } catch (error) {
      console.error('Could not copy team invitation code', error);
      button.textContent = '请手动选中邀请码复制';
    }
  });
});
document
  .querySelector('[data-fullscreen]')
  ?.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch (error) {
      console.error('Could not open the stage in fullscreen', error);
    }
  });
document.querySelectorAll('[data-dirty-form]').forEach((form) => {
  let dirty = false;
  form.addEventListener('input', () => {
    dirty = true;
  });
  form.addEventListener('submit', (event) => {
    queueMicrotask(() => {
      if (!event.defaultPrevented) dirty = false;
    });
  });
  window.addEventListener('beforeunload', (event) => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
});

const stage = document.querySelector('[data-stage-url]');
if (stage) {
  const timer = stage.querySelector('[data-stage-timer]');
  const phase = stage.querySelector('[data-stage-phase]');
  const sync = stage.querySelector('[data-stage-sync]');
  const labels = {
    idle: '等待开始',
    demo: '实机演示中',
    paused: '计时已暂停',
    transition: '请停止展示 · 评委评分 / 下一队接入',
    finished: '本轮时间已到 · 等待调度',
  };
  let clock = null;
  let receivedAt = 0;
  let connected = false;
  const tick = () => {
    if (!clock) return;
    const elapsed =
      connected && clock.running
        ? Math.floor((performance.now() - receivedAt) / 1000)
        : 0;
    const remaining = Math.max(0, clock.remaining - elapsed);
    timer.textContent = `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;
    timer.classList.toggle(
      'warning',
      remaining <= 15 && clock.phase !== 'idle',
    );
    phase.textContent = !connected
      ? '连接中断 · 请以现场调度为准'
      : remaining === 0 && clock.phase === 'demo'
        ? '展示时间已到 · 请停止展示'
        : labels[clock.phase];
  };
  const poll = async () => {
    try {
      const response = await fetch(stage.dataset.stageUrl, {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok)
        throw new Error(`Stage returned HTTP ${response.status}`);
      const data = await response.json();
      if (
        !data.clock ||
        !Number.isFinite(data.clock.remaining) ||
        !Array.isArray(data.upcoming)
      ) {
        throw new TypeError('Invalid stage response');
      }
      clock = data.clock;
      receivedAt = performance.now();
      connected = true;
      stage.querySelector('[data-stage-team]').textContent =
        data.current?.team || '等待下一队';
      stage.querySelector('[data-stage-title]').textContent =
        data.current?.project || '准备好，让作品说话。';
      const next = stage.querySelector('[data-stage-upcoming]');
      next.replaceChildren();
      data.upcoming.forEach((item, index) => {
        const card = document.createElement('div');
        card.className = 'next-card';
        const label = document.createElement('span');
        label.textContent = [
          'NEXT / 下一队候场',
          'UP NEXT / 设备检查',
          'GET READY / 提前准备',
        ][index];
        const name = document.createElement('strong');
        name.textContent = item.team;
        const detail = document.createElement('small');
        detail.textContent = `${item.code} · ${item.state}`;
        card.append(label, name, detail);
        next.append(card);
      });
      sync.textContent = '已与现场同步 · 每 2 秒更新';
      sync.classList.remove('error');
      tick();
    } catch (error) {
      console.error('Could not synchronize hackathon stage', error);
      connected = false;
      clock = null;
      timer.textContent = '--:--';
      phase.textContent = '连接中断 · 请以现场调度为准';
      sync.textContent = '连接中断，正在重试…';
      sync.classList.add('error');
    } finally {
      window.setTimeout(poll, 2000);
    }
  };
  window.setInterval(tick, 200);
  poll();
}

// Keep the clicked submit button's name/value in the POST while blocking repeats.
document.querySelectorAll('[data-once-form]').forEach(form => {
  let pending = false;
  form.addEventListener('submit', event => {
    if (pending) { event.preventDefault(); return; }
    queueMicrotask(() => {
      if (event.defaultPrevented) return;
      pending = true;
      form.querySelectorAll('[type=submit], button[name=action]').forEach(button => {
        button.setAttribute('aria-disabled', 'true');
      });
    });
  });
  window.addEventListener('pageshow', () => {
    pending = false;
    form.querySelectorAll('[aria-disabled=true]').forEach(button => button.removeAttribute('aria-disabled'));
  });
});
