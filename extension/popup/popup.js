let vacancyQueue = [];
let isRunning = false;
let serviceTabId = null;

document.addEventListener('DOMContentLoaded', async () => {
  const serverUrlEl = document.getElementById('serverUrl');
  const connBadge = document.getElementById('connBadge');
  const collectBtn = document.getElementById('collectBtn');
  const toggleBtn = document.getElementById('toggleBtn');
  const queueCountEl = document.getElementById('queueCount');
  const todayCountEl = document.getElementById('todayCount');
  const statusLogEl = document.getElementById('statusLog');

  const store = await chrome.storage.local.get(['serverUrl']);
  if (store.serverUrl) serverUrlEl.value = store.serverUrl;

  const sUrl = serverUrlEl.value.trim();
  const cfg = await checkServer(sUrl);

  async function checkServer(url) {
    try {
      const res = await fetch(`${url}/api/v1/config`);
      if (res.ok) {
        const data = await res.json();
        connBadge.innerText = "ONLINE";
        connBadge.className = "badge badge-on";
        todayCountEl.innerText = `${data.today_applied}/${data.daily_limit}`;
        return data;
      }
    } catch (e) {}
    connBadge.innerText = "OFFLINE";
    connBadge.className = "badge badge-off";
    return null;
  }

  // Кнопка 1: Открыть служебную вкладку поиска по фильтрам из Control Hub и собрать очередь
  collectBtn.addEventListener('click', async () => {
    const url = serverUrlEl.value.trim();
    const config = await checkServer(url);
    if (!config) return log("Сервер недоступен!");

    const search = config.search || {};
    let hhUrl = `https://hh.ru/search/vacancy?text=${encodeURIComponent(search.query || 'SRE')}`;
    if (search.area) hhUrl += `&area=${search.area}`;
    if (search.experience) hhUrl += `&experience=${search.experience}`;
    if (search.schedule) hhUrl += `&schedule=${search.schedule}`;

    log("Открываем служебную вкладку...");
    const tab = await chrome.tabs.create({ url: hhUrl, active: true });
    serviceTabId = tab.id;
    await waitForTabLoaded(serviceTabId);
    await sleep(3000);

    const depth = search.pages_depth || 2;
    vacancyQueue = [];

    for (let p = 1; p <= depth; p++) {
      log(`Сбор стр. ${p}/${depth}...`);
      const resp = await chrome.tabs.sendMessage(serviceTabId, {
        action: "COLLECT_VACANCY_LINKS",
        serverUrl: url
      });
      if (resp && resp.links) {
        vacancyQueue.push(...resp.links);
        vacancyQueue = Array.from(new Set(vacancyQueue));
        queueCountEl.innerText = vacancyQueue.length;
      }
      if (p < depth) {
        const nextRes = await chrome.tabs.sendMessage(serviceTabId, { action: "GO_NEXT_PAGE" });
        if (!nextRes || !nextRes.hasMore) break;
        await sleep(3200);
      }
    }
    log(`Готово: ${vacancyQueue.length} вакансий в очереди`);
  });

  // Кнопка 2: Запуск автооткликов в служебной вкладке с защитным оверлеем
  toggleBtn.addEventListener('click', async () => {
    if (isRunning) {
      isRunning = false;
      toggleBtn.innerText = "2. Запустить очередь";
      toggleBtn.className = "btn btn-primary";
      return log("Очередь остановлена.");
    }

    if (vacancyQueue.length === 0) {
      return log("Сначала нажмите '1. Собрать ссылки'");
    }

    isRunning = true;
    toggleBtn.innerText = "⏹ Остановить";
    toggleBtn.className = "btn btn-danger";

    const url = serverUrlEl.value.trim();
    if (!serviceTabId) {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      serviceTabId = activeTab.id;
    }

    while (vacancyQueue.length > 0 && isRunning) {
      const nextUrl = vacancyQueue.shift();
      queueCountEl.innerText = vacancyQueue.length;

      log(`Вкладка -> ${nextUrl.split('/').pop()}`);
      await chrome.tabs.update(serviceTabId, { url: nextUrl });
      await waitForTabLoaded(serviceTabId);
      await sleep(3500);

      try {
        const res = await chrome.tabs.sendMessage(serviceTabId, {
          action: "PROCESS_VACANCY",
          serverUrl: url,
          remaining: vacancyQueue.length
        });
        if (res && res.success) {
          log(`✅ Отклик! Скор: ${res.score}%`);
        } else if (res && res.skipped) {
          log(`⏭ Пропуск: ${res.reason}`);
        }
      } catch (e) {
        log(`Ошибка: ${e.message}`);
      }

      await checkServer(url);

      if (vacancyQueue.length > 0 && isRunning) {
        const pause = Math.floor(Math.random() * (32000 - 16000) + 16000);
        log(`Пауза ${Math.round(pause / 1000)} сек...`);
        await sleep(pause);
      }
    }

    isRunning = false;
    toggleBtn.innerText = "2. Запустить очередь";
    toggleBtn.className = "btn btn-primary";
    log("Очередь завершена!");
  });

  function log(txt) { statusLogEl.innerText = txt; }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function waitForTabLoaded(tabId) {
    return new Promise(resolve => {
      const l = (tId, ch) => {
        if (tId === tabId && ch.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(l);
          resolve();
        }
      };
      chrome.tabs.onUpdated.addListener(l);
    });
  }
});
