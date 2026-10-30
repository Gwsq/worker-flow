(() => {
  if (window.__worker_flow_injected) return;
  window.__worker_flow_injected = true;

  const DEFAULT_SERVER = "http://localhost:8000";

  // Если открыта Яндекс.Форма или Google Форма — показываем кнопку автозаполнения
  if (window.location.hostname.includes("forms.yandex") || window.location.href.includes("docs.google.com/forms")) {
    renderExternalFormHelper();
  }

  // Защитный оверлей служебной вкладки (Flow Tab Style)
  function showServiceOverlay(statusText, subText = "") {
    let ov = document.getElementById("worker-flow-overlay");
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "worker-flow-overlay";
      ov.style.cssText = `
        position: fixed; inset: 0; z-index: 2147483647;
        background: rgba(9, 9, 11, 0.72); backdrop-filter: blur(4px);
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        color: #fff; font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        pointer-events: all;
      `;
      ov.innerHTML = `
        <div style="background:#18181f; border:1px solid #272732; padding:24px 32px; border-radius:16px; text-align:center; max-width:420px; box-shadow:0 20px 50px rgba(0,0,0,0.6);">
          <div style="font-size:11px; color:#818cf8; font-weight:700; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px;">
            ⚡ Служебная вкладка Worker Flow
          </div>
          <div id="wf-ov-status" style="font-size:15px; font-weight:700; margin-bottom:6px;">Идет обработка вакансии...</div>
          <div id="wf-ov-sub" style="font-size:12px; color:#a1a1aa; margin-bottom:16px;">Пожалуйста, не кликайте в этой вкладке, чтобы не сбить автоматику</div>
          <button id="wf-ov-unlock" style="background:#272732; color:#e4e4e7; border:none; padding:6px 14px; border-radius:8px; font-size:11px; cursor:pointer;">
            Разблокировать вкладку вручную
          </button>
        </div>
      `;
      document.body.appendChild(ov);
      document.getElementById("wf-ov-unlock").addEventListener("click", () => ov.remove());
    }
    const st = document.getElementById("wf-ov-status");
    const sb = document.getElementById("wf-ov-sub");
    if (st) st.innerText = statusText;
    if (sb && subText) sb.innerText = subText;
  }

  function extractVacancyId(url) {
    const match = url.match(/\/vacancy\/(\d+)/);
    return match ? match[1] : null;
  }

  chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
    if (req.action === "COLLECT_VACANCY_LINKS") {
      showServiceOverlay("🔎 Сканируем поисковую выдачу hh.ru...", "Отсеиваем дубликаты через базу данных");
      collectFreshLinks(req.serverUrl || DEFAULT_SERVER)
        .then(links => sendResponse({ links }))
        .catch(err => sendResponse({ links: [], error: err.message }));
      return true;
    }

    if (req.action === "GO_NEXT_PAGE") {
      const nextBtn = document.querySelector('[data-qa="pager-next"]');
      if (nextBtn) {
        nextBtn.click();
        sendResponse({ hasMore: true });
      } else {
        sendResponse({ hasMore: false });
      }
      return true;
    }

    if (req.action === "PROCESS_VACANCY") {
      showServiceOverlay("🤖 AI анализирует вакансию и готовит письмо...", `Осталось в очереди: ${req.remaining || 0}`);
      processVacancy(req.serverUrl || DEFAULT_SERVER)
        .then(res => sendResponse(res))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }
  });

  async function collectFreshLinks(serverUrl) {
    const linkEls = Array.from(document.querySelectorAll(
      'a[data-qa="serp-item__title"], a[data-qa="vacancy-serp__vacancy-title"], a.bloko-link[href*="/vacancy/"]'
    ));
    const raw = Array.from(new Set(
      linkEls.map(a => a.href.split('?')[0]).filter(h => h && h.includes('/vacancy/'))
    ));

    const fresh = [];
    for (const url of raw) {
      const vId = extractVacancyId(url);
      if (vId) {
        try {
          const res = await fetch(`${serverUrl}/api/v1/check-duplicate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vacancy_id: vId })
          });
          const d = await res.json();
          if (!d.exists) fresh.push(url);
        } catch (e) {
          fresh.push(url);
        }
      }
    }
    return fresh;
  }

  async function processVacancy(serverUrl) {
    const vId = extractVacancyId(window.location.href);
    if (!vId) throw new Error("ID вакансии не найден");

    const already = document.querySelector('[data-qa="vacancy-response-link-view-topic"]') ||
                    Array.from(document.querySelectorAll('button, a, span')).some(
                      el => (el.innerText || "").includes("Вы уже откликнулись")
                    );
    if (already) {
      await report(serverUrl, { vacancy_id: vId, url: window.location.href, status: "APPLIED" });
      return { success: false, skipped: true, reason: "Уже откликались ранее" };
    }

    const descEl = await waitForElement(['[data-qa="vacancy-description"]', '.g-user-content', '.vacancy-description'], 7000);
    if (!descEl) throw new Error("Описание не загрузилось");

    const titleEl = document.querySelector('[data-qa="vacancy-title"]') || document.querySelector('h1');
    const compEl = document.querySelector('[data-qa="vacancy-company-name"]');

    const vTitle = titleEl ? titleEl.innerText.trim() : "Не указано";
    const vComp = compEl ? compEl.innerText.trim() : "Не указано";
    const vText = descEl.innerText.trim();

    const cfgRes = await fetch(`${serverUrl}/api/v1/config`);
    const cfg = await cfgRes.json();

    const res = await fetch(`${serverUrl}/api/v1/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        vacancy_id: vId,
        url: window.location.href,
        title: vTitle,
        company: vComp,
        full_text: `Позиция: ${vTitle}\nКомпания: ${vComp}\nОписание:\n${vText}`
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Ошибка сервера");
    }

    const decision = await res.json();
    if (!decision.should_apply) {
      showServiceOverlay(`⏭ Пропущено (${decision.score || 0}%)`, decision.reason);
      return { success: false, skipped: true, reason: decision.reason };
    }

    showServiceOverlay(`🎯 Скор ${decision.score}% — Отправляем отклик...`, `Выбрано резюме: ${decision.selected_resume_title}`);

    let applyBtn = await waitForElement([
      '[data-qa="vacancy-response-link-top"]',
      'a[data-qa="vacancy-response-link-top"]',
      'button[data-qa="vacancy-response-link-top"]',
      '.bloko-button_kind-primary'
    ], 3000);

    if (!applyBtn) applyBtn = await waitForButtonByText(["Откликнуться"], 3000);
    if (!applyBtn) throw new Error("Кнопка 'Откликнуться' не найдена");

    applyBtn.click();
    await delay(1800);

    if (decision.selected_resume_title) {
      await selectResume(decision.selected_resume_title);
    }

    let letterToggle = document.querySelector('[data-qa="vacancy-response-letter-toggle"]') ||
                       await waitForButtonByText(["Написать сопроводительное", "Добавить сопроводительное"], 1500);
    if (letterToggle && letterToggle.offsetParent !== null) {
      letterToggle.click();
      await delay(600);
    }

    const textarea = await waitForElement([
      '[data-qa="vacancy-response-popup-form-letter-input"]',
      'textarea[name="message"]',
      '.bloko-textarea'
    ], 2500);

    if (textarea) {
      setReactValue(textarea, decision.cover_letter);
      await delay(600);
    }

    if (cfg.permissions?.hh_tests !== false) {
      await solveQuestionnaire(serverUrl, cfg);
    }

    if (cfg.permissions?.auto_apply !== false) {
      let submitBtn = await waitForElement([
        '[data-qa="vacancy-response-submit-popup"]',
        '.bloko-modal-footer button.bloko-button_kind-primary'
      ], 2000);

      if (!submitBtn) submitBtn = await waitForButtonByText(["Отправить отклик", "Откликнуться", "Дальше"], 2000);
      if (submitBtn) {
        submitBtn.click();
        await delay(2500);

        if (window.location.href.includes('/questions') || document.querySelector('h1')?.innerText.includes("Ответьте на вопросы")) {
          await solveQuestionnaire(serverUrl, cfg);
          const fBtn = await waitForButtonByText(["Отправить", "Откликнуться"], 2000);
          if (fBtn) fBtn.click();
        }
      }
    }

    await report(serverUrl, {
      vacancy_id: vId,
      url: window.location.href,
      title: vTitle,
      company: vComp,
      score: decision.score,
      selected_resume: decision.selected_resume_title,
      reasoning: decision.reasoning || "",
      cover_letter: decision.cover_letter,
      status: "APPLIED"
    });

    return {
      success: true,
      score: decision.score,
      selectedResume: decision.selected_resume_title
    };
  }

  async function solveQuestionnaire(serverUrl, cfg) {
    const blocks = Array.from(document.querySelectorAll(
      '[data-qa="vacancy-response-questions-item"], .vacancy-response-question, fieldset, div[class*="question"]'
    )).filter(b => b.innerText && b.innerText.trim().length > 5);

    if (blocks.length === 0) return;
    const prefs = cfg.preferences || {};

    for (const b of blocks) {
      const qText = (b.innerText || "").toLowerCase();

      if (qText.includes("тг") || qText.includes("telegram") || qText.includes("телеграм")) {
        const input = b.querySelector('input[type="text"], textarea') ||
                      document.querySelector('input[placeholder*="Писать"], textarea[placeholder*="Писать"]');
        if (input) { setReactValue(input, cfg.telegram || "@onecappa"); continue; }
      }

      if (qText.includes("зп") || qText.includes("зарплат") || qText.includes("доход") || qText.includes("сумму")) {
        const input = b.querySelector('input[type="text"], textarea');
        if (input && cfg.salary) { setReactValue(input, cfg.salary); continue; }
      }

      const options = Array.from(b.querySelectorAll('label, [data-qa*="radio"], [data-qa*="checkbox"]'));
      if (options.length > 0) {
        let isYes = true;
        if ((qText.includes("ночн") || qText.includes("ночь")) && !prefs.night) isYes = false;
        if ((qText.includes("офис") || qText.includes("на месте")) && !prefs.office) isYes = false;

        const target = options.find(o => {
          const t = o.innerText.trim().toLowerCase();
          return isYes ? (t === "да" || t.startsWith("да,")) : (t === "нет" || t.startsWith("нет,"));
        });

        if (target) {
          const c = target.querySelector('input') || target;
          c.click();
          await delay(200);
        }
      }
    }
  }

  // Автозаполнение Яндекс.Форм и Google Forms
  function renderExternalFormHelper() {
    const btn = document.createElement("button");
    btn.innerText = "⚡ Заполнить анкету через Worker Flow";
    btn.style.cssText = `
      position: fixed; bottom: 24px; right: 24px; z-index: 999999;
      background: #6366f1; color: #fff; border: none; padding: 12px 18px;
      border-radius: 12px; font-weight: 700; font-size: 13px; cursor: pointer;
      box-shadow: 0 10px 25px rgba(99, 102, 241, 0.4);
    `;
    document.body.appendChild(btn);

    btn.addEventListener("click", async () => {
      btn.innerText = "⏳ AI заполняет поля...";
      const containers = Array.from(document.querySelectorAll('[role="listitem"], .SurveyPage-Question, .Question'));
      const questions = [];

      containers.forEach((c, idx) => {
        const input = c.querySelector('input[type="text"], input[type="email"], textarea');
        if (input) {
          questions.push({ id: String(idx), prompt: c.innerText.slice(0, 250) });
        }
      });

      const res = await fetch(`${DEFAULT_SERVER}/api/v1/solve-questions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ questions })
      });
      const data = await res.json();
      (data.answers || []).forEach(a => {
        const container = containers[parseInt(a.id, 10)];
        const input = container?.querySelector('input[type="text"], input[type="email"], textarea');
        if (input && a.answer) setReactValue(input, a.answer);
      });
      btn.innerText = "✅ Анкета заполнена!";
    });
  }

  async function selectResume(title) {
    const items = Array.from(document.querySelectorAll('[data-qa="resume-search-item"], .vacancy-response-popup__resume-item'));
    const m = items.find(i => i.innerText.toLowerCase().includes(title.toLowerCase()));
    if (m) {
      const c = m.querySelector('input[type="radio"]') || m;
      c.click();
      await delay(500);
    }
  }

  function setReactValue(el, val) {
    el.focus();
    const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, val);
    else el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  }

  async function report(serverUrl, data) {
    try {
      await fetch(`${serverUrl}/api/v1/report`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
    } catch (e) {}
  }

  function waitForElement(selectors, timeout = 5000) {
    const list = Array.isArray(selectors) ? selectors : [selectors];
    const s = Date.now();
    return new Promise(res => {
      const t = setInterval(() => {
        for (const sel of list) {
          try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) { clearInterval(t); return res(el); }
          } catch (e) {}
        }
        if (Date.now() - s > timeout) { clearInterval(t); res(null); }
      }, 250);
    });
  }

  function waitForButtonByText(variants, timeout = 4000) {
    const list = Array.isArray(variants) ? variants : [variants];
    const s = Date.now();
    return new Promise(res => {
      const t = setInterval(() => {
        const btns = Array.from(document.querySelectorAll('button, a, [role="button"], span'));
        for (const b of btns) {
          const txt = (b.innerText || "").trim().toLowerCase();
          if (list.some(v => txt.includes(v.toLowerCase())) && b.offsetParent !== null) {
            clearInterval(t);
            return res(b);
          }
        }
        if (Date.now() - s > timeout) { clearInterval(t); res(null); }
      }, 250);
    });
  }

  function delay(ms) { return new Promise(r => setTimeout(r, ms)); }
})();
