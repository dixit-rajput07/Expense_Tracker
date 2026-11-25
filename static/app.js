(() => {
  'use strict';

  // ---------- Config ----------
  const CATEGORY_COLORS = {
    Food: '#E4572E',
    Travel: '#2E86AB',
    Rent: '#1F2A5A',
    Shopping: '#9B5DE5',
    Bills: '#F5B301',
    Entertainment: '#17A398',
    Health: '#D64F8B',
    Education: '#5C8A2E',
    Other: '#8A90A6',
  };
  const colorFor = (category) => CATEGORY_COLORS[category] || '#8A90A6';

  // Har category ka apna example (Note field ka placeholder)
  const NOTE_EXAMPLES = {
    Food: 'Lunch with friends',
    Travel: 'Goa trip',
    Rent: 'PG rent for September',
    Shopping: 'New sneakers',
    Bills: 'Mobile recharge',
    Entertainment: 'Movie night',
    Health: 'Doctor visit and medicines',
    Education: 'Exam form fees',
    Other: 'Gift for a friend',
  };

  const money = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
  const compact = new Intl.NumberFormat('en-IN', { notation: 'compact' });

  // ---------- Small helpers ----------
  const $ = (id) => document.getElementById(id);
  const pad = (n) => String(n).padStart(2, '0');

  function todayISO() {
    // Local date (toISOString UTC deta hai, India mein subah galat din aa sakta hai)
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  const currentMonthKey = () => todayISO().slice(0, 7);

  function shiftMonth(key, delta) {
    const [y, m] = key.split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
  }
  function monthLabel(key) {
    const [y, m] = key.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' });
  }
  function shortMonth(key) {
    const [y, m] = key.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleString('en-IN', { month: 'short', year: '2-digit' });
  }
  function dayLabel(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleString('en-IN', { day: '2-digit', month: 'short' });
  }

  // DOM banane ka helper. textContent use hota hai, isliye user ka text safe rehta hai (XSS nahi).
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children) {
      if (child != null) node.append(child);
    }
    return node;
  }

  function say(target, text, isError = false) {
    target.textContent = text;
    target.classList.toggle('is-error', isError);
  }

  // Chhota popup jo apne aap gayab ho jata hai (jaise "Password changed successfully")
  function showToast(message, type = 'success', duration = 3000) {
    const stack = $('toast-stack');
    const node = el('div', { class: `toast ${type}`, role: 'status' },
      el('span', { class: 'toast-icon', 'aria-hidden': 'true', text: type === 'error' ? '\u26A0' : '\u2713' }),
      el('span', { text: message })
    );
    stack.append(node);
    const remove = () => node.remove();
    setTimeout(() => {
      node.classList.add('leaving');
      node.addEventListener('animationend', remove, { once: true });
      setTimeout(remove, 400); // reduced-motion: animation chalti hi nahi, isliye fallback
    }, duration);
  }

  async function api(url, options = {}) {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      ...options,
    });
    if (res.status === 401) {
      window.location.href = '/login';
      throw new Error('Please log in again.');
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
  }

  // ---------- State ----------
  const state = {
    month: currentMonthKey(),
    year: String(new Date().getFullYear()),
    editingId: null,
    salary: null,
    hasIncome: false,
    salaryPrompting: false,
    salaryEditing: false,
    expenses: [],
    pieChart: null,
    barChart: null,
    yearlyChart: null,
  };
  let loadId = 0;
  let yearlyLoadId = 0;

  function applyChartDefaults() {
    if (typeof Chart === 'undefined') return;
    const css = getComputedStyle(document.body);
    Chart.defaults.font.family = css.fontFamily;
    Chart.defaults.color = css.getPropertyValue('--muted').trim() || '#566080';
  }

  if (typeof Chart !== 'undefined') {
    applyChartDefaults();
  } else {
    // Internet nahi ya CDN block: khaali chart box hata do (category list phir bhi dikhegi)
    document.querySelectorAll('.chart-box').forEach((box) => { box.hidden = true; });
  }

  function updateNotePlaceholder() {
    const example = NOTE_EXAMPLES[$('category').value] || 'Add a short note';
    $('note').placeholder = `e.g. ${example}`;
  }

  // ---------- Loading data ----------
  function updateMonthUI() {
    $('month-name').textContent = monthLabel(state.month);
    $('next-month').disabled = state.month >= currentMonthKey();
  }

  async function load() {
    const myId = ++loadId;
    updateMonthUI();
    try {
      const [summary, expenses, tips, incomes] = await Promise.all([
        api(`/api/summary?month=${state.month}`),
        api(`/api/get_expenses?month=${state.month}`),
        api(`/api/insights?month=${state.month}`),
        api(`/api/get_incomes?month=${state.month}`),
      ]);
      if (myId !== loadId) return; // user ne tab tak doosra month khol liya
      state.expenses = expenses;
      $('total').textContent = money.format(summary.total);
      renderSalary(summary);
      renderIncomeList(incomes);
      renderSalaryUsed(summary);
      renderTips(tips.insights);
      renderBreakdown(summary);
      renderTrend(summary);
      renderBudgets(summary);
      renderLedger();
    } catch (err) {
      say($('page-msg'), err.message, true);
    }
  }

  // ---------- Rendering ----------
  function renderSalary(summary) {
    const hasSalary = summary.salary !== null;
    const hasIncome = summary.total_income !== null; // salary set ki gayi ho YA koi extra income entry ho
    // Login ke baad is mahine ki salary nahi hai aur skip bhi nahi ki: prompt khud khul jaye
    state.salary = summary.salary;
    state.hasIncome = hasIncome;
    state.salaryPrompting = !hasSalary && !summary.salary_skipped && state.month === currentMonthKey();

    $('salary-item').hidden = !hasSalary;
    $('income-item').hidden = !(summary.extra_income > 0);
    $('left-item').hidden = !hasIncome;
    $('salary-none').hidden = hasIncome;

    if (hasSalary) $('salary-value').textContent = money.format(summary.salary);
    if (summary.extra_income > 0) $('income-value').textContent = money.format(summary.extra_income);

    if (hasIncome) {
      const over = summary.balance < 0;
      $('left-label').textContent = over ? 'Over income by' : 'Left to spend';
      $('left-value').textContent = money.format(Math.abs(summary.balance));
      $('left-value').classList.toggle('is-over', over);
    } else {
      $('salary-none').textContent = `No income added for ${monthLabel(state.month)}.`;
    }
    $('salary-toggle').textContent = hasSalary ? 'Change salary' : 'Add salary';
    updateSalaryForm();
  }

  function renderIncomeList(incomes) {
    const list = $('income-list');
    list.replaceChildren();
    incomes.forEach((inc) => {
      list.append(
        el('li', {},
          el('span', { class: 'name' },
            el('span', { class: 'source', text: inc.source }),
            document.createTextNode(` \u2014 ${dayLabel(inc.date)}`)
          ),
          el('span', { class: 'amt', text: `+${money.format(inc.amount)}` }),
          el('button', {
            type: 'button', class: 'link-btn danger',
            'aria-label': `Delete ${inc.source} income of ${money.format(inc.amount)}`,
            text: 'Delete', onclick: () => removeIncome(inc),
          })
        )
      );
    });
  }

  async function removeIncome(inc) {
    const ok = window.confirm(`Delete ${money.format(inc.amount)} from ${inc.source}?`);
    if (!ok) return;
    try {
      await api(`/api/delete_income/${inc.id}`, { method: 'DELETE' });
      say($('page-msg'), 'Income deleted.');
      await load();
    } catch (err) {
      say($('page-msg'), err.message, true);
    }
  }

  const TIP_PREFIX = { alert: 'Alert: ', warn: 'Heads up: ', good: 'Good news: ', info: 'Note: ' };

  function renderTips(tips) {
    const list = $('tips-list');
    list.replaceChildren();
    tips.forEach((t) => {
      list.append(
        el('li', { class: `tip ${t.level}` },
          el('p', { class: 'tip-title' },
            el('span', { class: 'visually-hidden', text: TIP_PREFIX[t.level] || '' }),
            t.title
          ),
          el('p', { class: 'tip-text', text: t.text })
        )
      );
    });
  }

  function renderSalaryUsed(summary) {
    const has = summary.total_income !== null;
    $('salary-meter').hidden = !has;
    $('meter-empty').hidden = has;
    if (!has) return;

    const income = summary.total_income;
    const percent = (summary.total / income) * 100;
    const fill = Math.min(percent, 100);
    const left = money.format(Math.abs(summary.balance));
    const full = summary.balance <= 0; // poora income kharch (ya usse zyada)
    const level = full ? 'over' : percent >= 80 ? 'warning' : 'ok';

    let status = `${left} left`;
    if (summary.balance < 0) status = `Over income by ${left}`;
    else if (summary.balance === 0) status = 'Income fully spent';
    else if (level === 'warning') status = `Close to your income, ${left} left`;

    $('salary-meter').className = `salary-meter ${level}`;
    $('meter-percent').textContent = `${Math.round(percent)}%`;
    $('meter-amount').textContent = `${money.format(summary.total)} spent of ${money.format(income)}`;
    $('meter-fill').style.width = `${fill}%`;
    $('ring-fill').style.strokeDashoffset = String(100 - fill);
    $('ring-fill').style.visibility = fill > 0 ? 'visible' : 'hidden';
    $('meter-bar').setAttribute('aria-valuenow', String(Math.round(fill)));
    $('meter-status').textContent = status;
  }

  function updateSalaryForm() {
    const open = state.salaryPrompting || state.salaryEditing;
    $('salary-none').hidden = state.hasIncome || open; // form khula ho toh "No income" line dobara nahi
    $('salary-form').hidden = !open;
    $('salary-toggle').hidden = open;
    $('salary-skip').hidden = !state.salaryPrompting;
    $('salary-cancel').hidden = state.salaryPrompting;
    $('salary-form-title').textContent = state.salaryPrompting
      ? `Add your salary for ${monthLabel(state.month)}`
      : `Salary for ${monthLabel(state.month)}`;
    $('salary-form-hint').textContent = state.salaryPrompting
      ? 'Optional. Skip it now and add it later from this page.'
      : '';
  }

  function renderBreakdown(summary) {
    const rows = summary.by_category;
    const list = $('breakdown-list');
    list.replaceChildren();
    $('breakdown-chart-wrap').hidden = rows.length === 0;
    $('breakdown-empty').hidden = rows.length > 0;
    if (rows.length === 0) return;

    rows.forEach((r) => {
      list.append(
        el('li', {},
          el('span', { class: 'swatch', style: `background:${colorFor(r.category)}` }),
          el('span', { class: 'name', text: r.category }),
          el('span', { class: 'amt', text: money.format(r.total) })
        )
      );
    });

    if (typeof Chart === 'undefined') return; // offline: list phir bhi dikhegi
    const data = {
      labels: rows.map((r) => r.category),
      datasets: [{
        data: rows.map((r) => r.total),
        backgroundColor: rows.map((r) => colorFor(r.category)),
        borderColor: '#F4F6FB',
        borderWidth: 2,
      }],
    };
    if (state.pieChart) {
      state.pieChart.data = data;
      state.pieChart.update();
    } else {
      state.pieChart = new Chart($('pie-canvas'), {
        type: 'doughnut',
        data,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '62%',
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${money.format(ctx.parsed)}` } },
          },
        },
      });
    }
  }

  function renderTrend(summary) {
    const rows = summary.trend;
    $('trend-chart-wrap').hidden = rows.length === 0;
    $('trend-empty').hidden = rows.length > 0;
    if (rows.length === 0 || typeof Chart === 'undefined') return;

    const data = {
      labels: rows.map((r) => shortMonth(r.month)),
      datasets: [{
        data: rows.map((r) => r.total),
        backgroundColor: rows.map((r) => (r.month === state.month ? '#1F2A5A' : '#B3BBD2')),
        borderRadius: 4,
        maxBarThickness: 44,
      }],
    };
    if (state.barChart) {
      state.barChart.data = data;
      state.barChart.update();
    } else {
      state.barChart = new Chart($('bar-canvas'), {
        type: 'bar',
        data,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => money.format(ctx.parsed.y) } },
          },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, grid: { color: '#E2E6F2' }, ticks: { callback: (v) => compact.format(v) } },
          },
        },
      });
    }
  }

  // ---------- Yearly view ----------
  async function loadYearly() {
    const myId = ++yearlyLoadId;
    $('year-name').textContent = state.year;
    $('next-year').disabled = state.year >= String(new Date().getFullYear());
    try {
      const data = await api(`/api/yearly?year=${state.year}`);
      if (myId !== yearlyLoadId) return; // is beech mein user ne doosra saal khol liya
      renderYearly(data);
    } catch (err) {
      say($('page-msg'), err.message, true);
    }
  }

  function renderYearly(data) {
    $('yearly-total').textContent = money.format(data.total);
    const hasData = data.total > 0;
    $('yearly-chart-wrap').hidden = !hasData;
    $('yearly-empty').hidden = hasData;
    $('yearly-cat-wrap').hidden = !hasData;

    if (!hasData) return;

    const list = $('yearly-breakdown-list');
    list.replaceChildren();
    data.by_category.slice(0, 6).forEach((r) => {
      list.append(
        el('li', {},
          el('span', { class: 'swatch', style: `background:${colorFor(r.category)}` }),
          el('span', { class: 'name', text: r.category }),
          el('span', { class: 'amt', text: money.format(r.total) })
        )
      );
    });

    if (typeof Chart === 'undefined') return; // offline: total aur list phir bhi dikhte hain
    const chartData = {
      labels: data.months.map((m) => shortMonth(`${data.year}-${m.month}`)),
      datasets: [{
        data: data.months.map((m) => m.total),
        backgroundColor: '#1F2A5A',
        borderRadius: 4,
        maxBarThickness: 36,
      }],
    };
    if (state.yearlyChart) {
      state.yearlyChart.data = chartData;
      state.yearlyChart.update();
    } else {
      state.yearlyChart = new Chart($('yearly-canvas'), {
        type: 'bar',
        data: chartData,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => money.format(ctx.parsed.y) } },
          },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, grid: { color: '#E2E6F2' }, ticks: { callback: (v) => compact.format(v) } },
          },
        },
      });
    }
  }

  // ---------- Compare two months ----------
  function diffClass(diff) {
    if (diff > 0) return 'is-up';    // zyada kharcha hua
    if (diff < 0) return 'is-down';  // kam kharcha hua
    return '';
  }

  function formatDiff(diff) {
    if (diff === 0) return 'No change';
    return `${diff > 0 ? '+' : '-'}${money.format(Math.abs(diff))}`;
  }

  function renderCompare(data) {
    $('compare-results').hidden = false;
    $('compare-label-a').textContent = monthLabel(data.month_a);
    $('compare-label-b').textContent = monthLabel(data.month_b);
    $('compare-th-a').textContent = shortMonth(data.month_a);
    $('compare-th-b').textContent = shortMonth(data.month_b);
    $('compare-total-a').textContent = money.format(data.total_a);
    $('compare-total-b').textContent = money.format(data.total_b);
    $('compare-diff').textContent = formatDiff(data.diff);
    $('compare-diff').className = `money-value ${diffClass(data.diff)}`;

    const body = $('compare-body');
    body.replaceChildren();
    if (data.by_category.length === 0) {
      body.append(el('tr', {}, el('td', { colspan: '4', class: 'empty', text: 'No expenses in either month.' })));
      return;
    }
    data.by_category.forEach((r) => {
      body.append(
        el('tr', {},
          el('td', {},
            el('span', { class: 'swatch', style: `background:${colorFor(r.category)}` }),
            r.category
          ),
          el('td', { class: 'col-amt', text: money.format(r.a) }),
          el('td', { class: 'col-amt', text: money.format(r.b) }),
          el('td', { class: `col-amt ${diffClass(r.diff)}`, text: formatDiff(r.diff) })
        )
      );
    });
  }

  function renderBudgets(summary) {
    const list = $('budget-list');
    list.replaceChildren();
    $('budget-empty').hidden = summary.budgets.length > 0;

    summary.budgets.forEach((b) => {
      const pct = Math.min(b.percent, 100);
      let statusText = `${Math.round(b.percent)}% used`;
      if (b.status === 'warning') statusText = `${Math.round(b.percent)}% used, close to the limit`;
      if (b.status === 'over') statusText = `Over budget by ${money.format(b.spent - b.limit)}`;

      list.append(
        el('li', { class: `budget ${b.status}` },
          el('div', { class: 'budget-head' },
            el('span', { text: b.category }),
            el('span', { class: 'amt', text: `${money.format(b.spent)} of ${money.format(b.limit)}` })
          ),
          el('div', {
            class: 'bar',
            role: 'progressbar',
            'aria-label': `${b.category} budget used`,
            'aria-valuemin': '0',
            'aria-valuemax': '100',
            'aria-valuenow': String(Math.round(pct)),
          }, el('div', { class: 'fill', style: `width:${pct}%` })),
          el('p', { class: 'status', text: statusText })
        )
      );
    });
  }

  function renderLedger() {
    const body = $('ledger-body');
    body.replaceChildren();
    const has = state.expenses.length > 0;
    $('ledger-table-wrap').hidden = !has;
    $('ledger-empty').hidden = has;
    $('ledger-empty').textContent = `No expenses in ${monthLabel(state.month)} yet. Add one with the form.`;

    state.expenses.forEach((e) => {
      const when = dayLabel(e.date);
      body.append(
        el('tr', {},
          el('td', { class: 'col-date', text: when }),
          el('td', {},
            el('span', { class: 'swatch', style: `background:${colorFor(e.category)}` }),
            e.category
          ),
          el('td', { text: e.note || '' }),
          el('td', { class: 'col-amt', text: money.format(e.amount) }),
          el('td', { class: 'col-actions' },
            el('button', {
              type: 'button',
              class: 'link-btn',
              'aria-label': `Edit ${e.category} expense on ${when}`,
              text: 'Edit',
              onclick: () => startEdit(e),
            }),
            el('button', {
              type: 'button',
              class: 'link-btn danger',
              'aria-label': `Delete ${e.category} expense on ${when}`,
              text: 'Delete',
              onclick: () => removeExpense(e),
            })
          )
        )
      );
    });
  }

  // ---------- Add / edit / delete ----------
  function resetForm() {
    state.editingId = null;
    $('expense-form').reset();
    $('date').value = todayISO();
    updateNotePlaceholder();
    $('form-title').textContent = 'Add expense';
    $('save-btn').textContent = 'Save expense';
    $('cancel-edit').hidden = true;
  }

  function startEdit(e) {
    state.editingId = e.id;
    $('amount').value = e.amount;
    $('category').value = e.category;
    $('date').value = e.date;
    $('note').value = e.note || '';
    updateNotePlaceholder();
    $('form-title').textContent = 'Edit expense';
    $('save-btn').textContent = 'Save changes';
    $('cancel-edit').hidden = false;
    say($('form-msg'), '');
    $('add-expense').scrollIntoView({ block: 'center' });
    $('amount').focus();
  }

  async function removeExpense(e) {
    const ok = window.confirm(`Delete ${money.format(e.amount)} for ${e.category} on ${dayLabel(e.date)}?`);
    if (!ok) return;
    try {
      await api(`/api/delete_expense/${e.id}`, { method: 'DELETE' });
      if (state.editingId === e.id) resetForm();
      say($('page-msg'), 'Expense deleted.');
      await load();
    } catch (err) {
      say($('page-msg'), err.message, true);
    }
  }

  $('expense-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const payload = {
      amount: $('amount').value,
      category: $('category').value,
      date: $('date').value,
      note: $('note').value.trim(),
    };
    const editing = state.editingId !== null;
    const btn = $('save-btn');
    btn.disabled = true;
    try {
      if (editing) {
        await api(`/api/update_expense/${state.editingId}`, { method: 'PUT', body: JSON.stringify(payload) });
      } else {
        await api('/api/add_expense', { method: 'POST', body: JSON.stringify(payload) });
      }
      state.month = payload.date.slice(0, 7); // us mahine par jao jahan expense save hua
      resetForm();
      say($('page-msg'), '');
      say($('form-msg'), editing ? 'Changes saved.' : 'Expense saved.');
      await load();
    } catch (err) {
      say($('form-msg'), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $('category').addEventListener('change', updateNotePlaceholder);

  $('cancel-edit').addEventListener('click', () => {
    resetForm();
    say($('form-msg'), '');
  });

  // ---------- Budgets ----------
  $('budget-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try {
      await api('/api/budget', {
        method: 'POST',
        body: JSON.stringify({
          category: $('budget-category').value,
          monthly_limit: $('budget-limit').value,
        }),
      });
      $('budget-limit').value = '';
      say($('budget-msg'), 'Budget saved.');
      await load();
    } catch (err) {
      say($('budget-msg'), err.message, true);
    }
  });

  // ---------- Salary (optional) ----------
  $('salary-toggle').addEventListener('click', () => {
    state.salaryEditing = true;
    $('salary-amount').value = state.salary ?? '';
    say($('salary-msg'), '');
    updateSalaryForm();
    $('salary-amount').focus();
  });

  $('salary-cancel').addEventListener('click', () => {
    state.salaryEditing = false;
    updateSalaryForm();
  });

  $('salary-skip').addEventListener('click', async () => {
    try {
      await api('/api/salary/skip', { method: 'POST', body: JSON.stringify({ month: state.month }) });
      await load();
    } catch (err) {
      say($('salary-msg'), err.message, true);
    }
  });

  $('salary-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('salary-save');
    btn.disabled = true;
    try {
      await api('/api/salary', {
        method: 'POST',
        body: JSON.stringify({ month: state.month, amount: $('salary-amount').value }),
      });
      state.salaryEditing = false;
      $('salary-amount').value = '';
      say($('salary-msg'), '');
      say($('page-msg'), 'Salary saved.');
      await load();
    } catch (err) {
      say($('salary-msg'), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- Extra income (optional, multiple entries per month) ----------
  $('income-toggle').addEventListener('click', () => {
    $('income-form').hidden = false;
    $('income-toggle').hidden = true;
    $('income-date').value = todayISO();
    say($('income-msg'), '');
    $('income-amount').focus();
  });

  $('income-cancel').addEventListener('click', () => {
    $('income-form').hidden = true;
    $('income-toggle').hidden = false;
    $('income-form').reset();
    say($('income-msg'), '');
  });

  $('income-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      await api('/api/add_income', {
        method: 'POST',
        body: JSON.stringify({
          amount: $('income-amount').value,
          source: $('income-source').value.trim(),
          date: $('income-date').value,
        }),
      });
      $('income-form').reset();
      $('income-form').hidden = true;
      $('income-toggle').hidden = false;
      say($('income-msg'), '');
      say($('page-msg'), 'Income added.');
      await load();
    } catch (err) {
      say($('income-msg'), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- Month switcher ----------
  function closeIncomeForm() {
    $('income-form').hidden = true;
    $('income-toggle').hidden = false;
    $('income-form').reset();
    say($('income-msg'), '');
  }

  $('prev-month').addEventListener('click', () => {
    state.month = shiftMonth(state.month, -1);
    state.salaryEditing = false;
    closeIncomeForm();
    say($('salary-msg'), '');
    say($('page-msg'), '');
    load();
  });
  $('next-month').addEventListener('click', () => {
    state.month = shiftMonth(state.month, 1);
    state.salaryEditing = false;
    closeIncomeForm();
    say($('salary-msg'), '');
    say($('page-msg'), '');
    load();
  });

  // ---------- Profile menu (dropdown + dark mode + change password) ----------
  const profileTrigger = $('profile-trigger');
  const profileDropdown = $('profile-dropdown');
  const pwdTrigger = $('change-password-trigger');
  const pwdDropdown = $('password-dropdown');
  const profileEditTrigger = $('edit-profile-trigger');
  const profileFormDropdown = $('profile-form-dropdown');
  const exportTrigger = $('export-trigger');
  const exportDropdown = $('export-dropdown');

  function closeMenus() {
    profileDropdown.hidden = true;
    profileTrigger.setAttribute('aria-expanded', 'false');
    pwdDropdown.hidden = true;
    pwdTrigger.setAttribute('aria-expanded', 'false');
    profileFormDropdown.hidden = true;
    profileEditTrigger.setAttribute('aria-expanded', 'false');
    exportDropdown.hidden = true;
    exportTrigger.setAttribute('aria-expanded', 'false');
  }

  profileTrigger.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const willOpen = profileDropdown.hidden;
    closeMenus();
    if (willOpen) {
      profileDropdown.hidden = false;
      profileTrigger.setAttribute('aria-expanded', 'true');
    }
  });

  pwdTrigger.addEventListener('click', (ev) => {
    ev.stopPropagation();
    profileDropdown.hidden = true;
    profileTrigger.setAttribute('aria-expanded', 'false');
    pwdDropdown.hidden = false;
    pwdTrigger.setAttribute('aria-expanded', 'true');
    $('password-form').reset();
    say($('password-msg'), '');
    $('current-password').focus();
  });

  $('password-cancel').addEventListener('click', closeMenus);

  profileEditTrigger.addEventListener('click', (ev) => {
    ev.stopPropagation();
    profileDropdown.hidden = true;
    profileTrigger.setAttribute('aria-expanded', 'false');
    profileFormDropdown.hidden = false;
    profileEditTrigger.setAttribute('aria-expanded', 'true');
    $('profile-username').value = $('profile-name').textContent;
    say($('profile-msg'), '');
    $('profile-username').focus();
  });

  $('profile-cancel').addEventListener('click', closeMenus);

  $('profile-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const username = $('profile-username').value.trim();
    const btn = ev.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const res = await api('/api/profile', { method: 'POST', body: JSON.stringify({ username }) });
      $('profile-name').textContent = res.username;
      closeMenus();
      showToast('Profile updated successfully', 'success');
    } catch (err) {
      say($('profile-msg'), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  document.addEventListener('click', (ev) => {
    if (!profileDropdown.hidden && !profileDropdown.contains(ev.target)) closeMenus();
    if (!pwdDropdown.hidden && !pwdDropdown.contains(ev.target)) closeMenus();
    if (!profileFormDropdown.hidden && !profileFormDropdown.contains(ev.target)) closeMenus();
    if (!exportDropdown.hidden && !exportDropdown.contains(ev.target) && ev.target !== exportTrigger) closeMenus();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closeMenus();
  });

  // ---------- Export (CSV / Excel / PDF) ----------
  exportTrigger.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const willOpen = exportDropdown.hidden;
    closeMenus();
    if (willOpen) {
      exportDropdown.hidden = false;
      exportTrigger.setAttribute('aria-expanded', 'true');
    }
  });

  exportDropdown.querySelectorAll('[data-format]').forEach((link) => {
    link.addEventListener('click', (ev) => {
      ev.preventDefault();
      const format = link.dataset.format;
      window.location.href = `/api/export?month=${state.month}&format=${format}`;
      closeMenus();
    });
  });

  // ---------- Import (CSV) ----------
  const importTrigger = $('import-trigger');
  const importFile = $('import-file');

  importTrigger.addEventListener('click', () => {
    importFile.value = '';
    importFile.click();
  });

  importFile.addEventListener('change', async () => {
    const file = importFile.files[0];
    if (!file) return;

    say($('import-msg'), '');
    importTrigger.disabled = true;
    importTrigger.textContent = 'Importing...';

    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/import_expenses', {
        method: 'POST',
        credentials: 'same-origin',
        body: formData,
      });
      if (res.status === 401) {
        window.location.href = '/login';
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Import failed. Please try again.');

      const parts = [`${data.inserted} expense${data.inserted === 1 ? '' : 's'} imported`];
      if (data.skipped) parts.push(`${data.skipped} row${data.skipped === 1 ? '' : 's'} skipped`);
      showToast(parts.join(', '), data.skipped ? 'error' : 'success');

      if (data.errors && data.errors.length) {
        say($('import-msg'), data.errors.join(' \u2022 '), true);
      } else {
        say($('import-msg'), '');
      }

      if (data.inserted) load(); // ledger/summary refresh karo taaki naye rows turant dikhein
    } catch (err) {
      say($('import-msg'), err.message, true);
    } finally {
      importTrigger.disabled = false;
      importTrigger.textContent = 'Import CSV';
    }
  });

  // Dark mode: turant apply hota hai aur agli visit ke liye yaad rehta hai
  const darkToggle = $('dark-mode-toggle');
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    darkToggle.setAttribute('aria-checked', String(theme === 'dark'));
  }
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  darkToggle.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem('theme', next); } catch (err) { /* private browsing: ignore */ }
    if (state.pieChart) { state.pieChart.destroy(); state.pieChart = null; }
    if (state.barChart) { state.barChart.destroy(); state.barChart = null; }
    applyChartDefaults();
    load(); // charts ke colors theme ke hisaab se firse banao
  });

  // ---------- Yearly view: year switcher ----------
  $('prev-year').addEventListener('click', () => {
    state.year = String(Number(state.year) - 1);
    loadYearly();
  });
  $('next-year').addEventListener('click', () => {
    state.year = String(Number(state.year) + 1);
    loadYearly();
  });

  // ---------- Compare two months ----------
  $('compare-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const monthA = $('compare-month-a').value;
    const monthB = $('compare-month-b').value;
    if (!monthA || !monthB) return;
    const btn = ev.target.querySelector('button[type=submit]');
    btn.disabled = true;
    say($('compare-msg'), '');
    try {
      const data = await api(`/api/compare?month_a=${monthA}&month_b=${monthB}`);
      renderCompare(data);
    } catch (err) {
      $('compare-results').hidden = true;
      say($('compare-msg'), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $('password-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const current = $('current-password').value;
    const next = $('new-password').value;
    const confirm = $('confirm-password').value;
    if (next !== confirm) {
      say($('password-msg'), 'New passwords do not match.', true);
      return;
    }
    const btn = ev.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      await api('/api/change_password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      closeMenus();
      showToast('Password changed successfully', 'success');
    } catch (err) {
      say($('password-msg'), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- Start ----------
  resetForm();
  $('compare-month-a').value = currentMonthKey();
  $('compare-month-b').value = shiftMonth(currentMonthKey(), -1);
  load();
  loadYearly();
})();
