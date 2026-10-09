/* ============================================================
   RESOURCE ALLOCATION ANALYSIS — UI (admin only), inside AI Insights
   Depends on: resource-engine.js, ai-insights.js (window.AIInsightsCtx),
   Chart.js, and the dashboard globals apiGet / showToast / formatDisplayDate.
   Decision-support only: this file never buys, orders, distributes,
   or changes inventory. It only reads aggregated records.
   ============================================================ */
(function () {
    'use strict';
    const RE = window.ResourceEngine;
    const $ = id => document.getElementById(id);
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const NAVY = '#0b1f4b', BLUE = '#2255c4', MUTED = '#8a97b3', RED = '#c0392b', ORANGE = '#e08a1e', TEAL = '#14b8a6', PALE = '#b8c4de';

    const R = { key: '', data: null, result: null, sub: 'frequent', item: '', disease: '', charts: {}, token: 0, loading: false, error: '' };
    const SUBS = [['frequent', 'Frequently Used'], ['seasonal', 'Seasonal Monitoring'], ['graphs', 'Usage Graphs'], ['alerts', 'Monitoring Alerts']];
    const TREND_TXT = { increasing: 'Increasing', decreasing: 'Decreasing', stable: 'Stable', insufficient: 'Not enough data' };
    const LEVEL_TEXT = { none: 'No data', limited: 'Limited data', moderate: 'Moderate data', good: 'Sufficient data' };
    const HSTATUS = { supported: 'Supported by records', limited: 'Limited evidence', none: 'No clear rise', insufficient: 'Insufficient data', no_cases: 'No cases recorded' };
    const ctx = () => window.AIInsightsCtx;
    const rangeText = () => `${formatDisplayDate(R.result.range.start)} – ${formatDisplayDate(R.result.range.end)}`;
    const num = n => (n == null ? '—' : Number(n).toLocaleString());
    const stockBadge = s => `<span class="res-stock st-${s.status}">${esc(s.label)}</span>`;
    const trendCell = t => `<span class="ai-pat pat-${t.status}">${TREND_TXT[t.status]}${t.status === 'increasing' || t.status === 'decreasing' ? (t.pct != null ? ` (${t.pct > 0 ? '+' : ''}${t.pct}%)` : '') : ''}</span>`;
    const empty = t => `<div class="ai-empty">${t}</div>`;

    /* ---------- entry points (called from ai-insights.js) ---------- */
    window.ResourceUI = {
        html() { return '<div id="resRoot"></div>'; },
        mount() { load(); },
        print() { printReport(); }
    };

    async function load() {
        const root = $('resRoot'); if (!root) return;
        const { start, end } = ctx().range();
        if (!start || !end) return;
        const key = `${start}|${end}`;
        if (R.key === key && R.result) { render(); return; }
        const tk = ++R.token; R.loading = true; R.error = '';
        root.innerHTML = empty('Analyzing inventory and dispensing records…');
        let data = null;
        try { data = await apiGet(`/api/ai-insights/resources?start_date=${start}&end_date=${end}`); } catch (_) { }
        if (tk !== R.token) return;
        R.loading = false;
        if (!data || !Array.isArray(data.items)) {
            R.result = null; R.key = '';
            const r2 = $('resRoot'); if (r2) r2.innerHTML = empty('Resource data could not be loaded. Confirm you are signed in as an administrator and that the <code>/api/ai-insights/resources</code> endpoint is running.');
            return;
        }
        R.data = data; R.key = key;
        R.result = RE.analyze(data, { start, end, today: new Date() });
        const top = R.result.summary.topMedicine || R.result.used[0];
        R.item = top ? String(top.id) : '';
        R.disease = (top && R.result.assoc.get(top.id).topDisease) || (R.result.diseases[0] || {}).name || '';
        render();
    }

    /* ---------- shell ---------- */
    function render() {
        const root = $('resRoot'); if (!root || !R.result) return;
        const r = R.result, s = r.sufficiency;
        root.innerHTML = `
        <div class="ai-banner lvl-${s.level === 'none' ? 'insufficient' : s.level}">
            <div class="ai-banner-main"><span class="ai-pill">${LEVEL_TEXT[s.level]}</span><span>${esc(s.message)}</span></div>
            <div class="ai-banner-stats">${num(r.totals.units)} units dispensed • ${r.totals.itemsUsed} items used • ${num(r.totals.doses)} vaccine doses recorded • ${s.months} complete months</div>
            ${s.notes.map(n => `<div class="ai-note">${esc(n)}</div>`).join('')}
        </div>
        <div class="ai-legend">
            <span><span class="ai-tag rec">Recorded Data</span> dispensed quantities and stock stored in the system</span>
            <span><span class="ai-tag obs">Observed Pattern</span> pattern found in historical records</span>
        </div>
        <div class="chart-tabbar"><div class="chart-tabs">${SUBS.map(([k, l]) => `<button class="chart-tab ${R.sub === k ? 'active' : ''}" data-ressub="${k}">${l}</button>`).join('')}</div></div>
        <div id="resPanels"></div>
        <div class="ai-disclaimer">Resource Allocation Analysis is decision-support only. It does not purchase, order, distribute, or change inventory, and it does not state that a medicine is necessary for a disease. Findings come from recorded inventory, dispensing, and disease data; interpretation and decisions remain with authorized health workers and administrators.</div>`;
        renderSub();
    }

    function renderSub() {
        Object.values(R.charts).forEach(c => c.destroy()); R.charts = {};
        const p = $('resPanels'); if (!p) return;
        if (R.result.sufficiency.level === 'none' && !R.result.vaccines.length && R.sub !== 'alerts') { p.innerHTML = empty('No dispensing or vaccine records were found for the selected period. Dispensed prescriptions and recorded immunizations will appear here.'); return; }
        p.innerHTML = { frequent: subFrequent, seasonal: subSeasonal, graphs: subGraphs, alerts: subAlerts }[R.sub]();
        if (R.sub === 'seasonal') mountSeasonal();
        if (R.sub === 'graphs') mountGraphs();
    }

    /* ---------- chart builders (shared by screen and print) ---------- */
    const base = () => ({ responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } }, datalabels: { display: false } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } } });
    const mk = (key, id, cfg) => { const el = $(id); if (el) R.charts[key] = new Chart(el, cfg); };
    function imgOf(cfg, w = 900, h = 300) {
        const c = document.createElement('canvas'); c.width = w; c.height = h;
        cfg.options = Object.assign({}, cfg.options, { responsive: false, animation: false, devicePixelRatio: 1 });
        cfg.plugins = [{ id: 'bg', beforeDraw(ch) { const x = ch.ctx; x.save(); x.globalCompositeOperation = 'destination-over'; x.fillStyle = '#fff'; x.fillRect(0, 0, ch.width, ch.height); x.restore(); } }];
        const ch = new Chart(c, cfg); const u = ch.toBase64Image(); ch.destroy(); return u;
    }
    const itemById = id => R.result.used.find(m => String(m.id) === String(id));
    const diseaseByName = n => R.result.diseases.find(d => d.name === n);

    function catConfig() {
        const r = R.result, col = { Medicine: NAVY, Vaccine: TEAL, 'Medical Supply': BLUE };
        const o = base(); o.scales.x.stacked = true; o.scales.y.stacked = true;
        return { type: 'bar', data: { labels: r.monthLabels, datasets: r.catUsage.filter(c => c.series.some(v => v > 0)).map(c => ({ label: c.category === 'Vaccine' ? 'Vaccine (dispensed from inventory)' : c.category, data: c.series, backgroundColor: col[c.category] })) }, options: o };
    }
    function topConfig() {
        const a = R.result.used.slice(0, 10);
        return { type: 'bar', data: { labels: a.map(m => m.name), datasets: [{ label: 'Quantity dispensed', data: a.map(m => m.total), backgroundColor: a.map((_, i) => i === 0 ? NAVY : PALE), borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, datalabels: { anchor: 'end', align: 'end', color: NAVY, font: { size: 10 }, formatter: v => v } },
                scales: { x: { beginAtZero: true, grace: '12%', ticks: { precision: 0 } }, y: { grid: { display: false } } } } };
    }
    function itemConfig(m) {
        const r = R.result, s = m.series, ma = s.map((_, i) => i < 2 ? null : Math.round(s.slice(i - 2, i + 1).reduce((a, b) => a + b, 0) / 3 * 10) / 10);
        const up = s.map((v, i) => i > 0 && v > s[i - 1] && v >= (s[i - 1] || 0) * 1.25 && v - s[i - 1] >= 1);
        const o = base();
        return { type: 'line', data: { labels: r.monthLabels, datasets: [
            { label: `${m.name} (${m.unit})`, data: s, borderColor: NAVY, backgroundColor: NAVY, borderWidth: 2, tension: .2, pointRadius: s.map((_, i) => up[i] ? 6 : 3), pointBackgroundColor: s.map((_, i) => up[i] ? ORANGE : NAVY), pointBorderColor: s.map((_, i) => up[i] ? ORANGE : NAVY) },
            { label: 'Observed trend (3-month average)', data: ma, borderColor: MUTED, borderDash: [6, 4], borderWidth: 2, pointRadius: 0, tension: .3 }] }, options: o };
    }
    function stockConfig() {
        const a = R.result.used.slice(0, 8);
        return { type: 'bar', data: { labels: a.map(m => m.name), datasets: [
            { label: 'Current stock', data: a.map(m => m.stock.stock ?? 0), backgroundColor: NAVY, borderRadius: 3 },
            { label: 'Average monthly usage', data: a.map(m => m.avgMonthly), backgroundColor: ORANGE, borderRadius: 3 },
            { label: 'Reorder level', data: a.map(m => m.stock.reorder ?? 0), backgroundColor: PALE, borderRadius: 3 }] },
            options: Object.assign(base(), { indexAxis: 'y' }) };
    }
    function seasonConfig(m, win) {
        return { type: 'bar', data: { labels: RE.MN, datasets: [{ label: `Average ${m.unit} per month`, data: R.result.calendar(m), backgroundColor: RE.MN.map((_, i) => win.includes(i + 1) ? NAVY : PALE), borderRadius: 3 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, datalabels: { anchor: 'end', align: 'top', color: NAVY, font: { size: 10 }, formatter: v => v == null ? '' : v } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grace: '12%' } } } };
    }
    function assocConfig(m, d) {
        const r = R.result;
        return { type: 'line', data: { labels: r.monthLabels, datasets: [
            { label: `${m.name} dispensed (${m.unit})`, data: m.series, borderColor: NAVY, backgroundColor: NAVY, borderWidth: 2, tension: .2, pointRadius: 3, yAxisID: 'y' },
            { label: `${d.name} recorded cases`, data: d.series, borderColor: ORANGE, backgroundColor: ORANGE, borderWidth: 2, borderDash: [5, 4], tension: .2, pointRadius: 3, yAxisID: 'y1' }] },
            options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
                plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } }, datalabels: { display: false } },
                scales: { x: { grid: { display: false } }, y: { beginAtZero: true, position: 'left', ticks: { precision: 0 }, title: { display: true, text: 'Dispensed', font: { size: 10 } } },
                    y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, ticks: { precision: 0 }, title: { display: true, text: 'Cases', font: { size: 10 } } } } } };
    }

    /* ============ SUB 1: Frequently used resources ============ */
    function rowsFor(arr, kind) {
        if (!arr.length) return `<tr><td colspan="7" class="ai-none">No ${kind} recorded in this period.</td></tr>`;
        return arr.slice(0, 12).map(m => `<tr><td>${esc(m.name)}${m.consistentHigh ? ' <span class="ai-tag obs">Consistently high</span>' : ''}</td>
            <td>${num(m.total)} ${esc(m.unit)}</td><td>${m.avgMonthly}</td><td>${trendCell(m.trend)}</td>
            <td>${m.stock.stock != null ? num(m.stock.stock) : '—'}</td><td>${stockBadge(m.stock)}</td><td>${m.monthsActive} of ${R.result.months.length}</td></tr>`).join('');
    }
    const THEAD = '<thead><tr><th>Item</th><th>Quantity used</th><th>Avg / month</th><th>Usage trend</th><th>Current stock</th><th>Stock status</th><th>Months used</th></tr></thead>';

    function featured(m, label) {
        if (!m) return '';
        return `<div class="card"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">${esc(label)} — ${esc(m.name)}</div><div class="sec-sub">Usage period — ${esc(R.result.period)}</div></div><span class="ai-tag rec">Recorded Data</span></div>
            <div class="res-kpis">
                <div><span>Quantity used</span><b>${num(m.total)} ${esc(m.unit)}</b></div>
                <div><span>Usage trend</span><b>${TREND_TXT[m.trend.status]}</b></div>
                <div><span>Current stock</span><b>${num(m.stock.stock)} ${esc(m.unit)}</b>${stockBadge(m.stock)}</div>
                <div><span>Months with usage</span><b>${m.monthsActive} of ${R.result.months.length}</b></div></div>
            <div class="ai-insight lvl-${m.stock.status === 'low' || m.stock.status === 'out' ? 'moderate' : 'routine'}">
                <div class="ai-insight-head"><span class="ai-lvl">INSIGHT</span><span class="ai-src">Generated from recorded data</span></div><p>${esc(m.insight)}</p></div></div></div>`;
    }

    function subFrequent() {
        const r = R.result, S = r.summary, li = (arr, f) => arr.length ? arr.slice(0, 8).map(f).join('') : '<li class="ai-none">None identified in this period</li>';
        const med = r.used.filter(m => m.category === 'Medicine'), sup = r.used.filter(m => m.category === 'Medical Supply'), vi = r.used.filter(m => m.category === 'Vaccine');
        return `${featured(S.topMedicine, 'Most Frequently Used Medicine') || empty('No medicines were dispensed in this period.')}
        <div class="card ai-gap"><div class="card-body"><div class="ai-head"><div class="card-title">Medicines</div><span class="ai-tag rec">Recorded Data</span></div>
            <div class="table-wrap"><table>${THEAD}<tbody>${rowsFor(med, 'dispensed medicines')}</tbody></table></div></div></div>
        <div class="card ai-gap"><div class="card-body"><div class="ai-head"><div class="card-title">Vaccines administered</div><span class="ai-tag rec">Recorded Data</span></div>
            <div class="table-wrap"><table><thead><tr><th>Vaccine</th><th>Doses recorded</th><th>Avg / month</th><th>Trend</th><th>Inventory stock</th><th>Stock status</th></tr></thead><tbody>
            ${r.vaccines.length ? r.vaccines.slice(0, 12).map(v => `<tr><td>${esc(v.name)}</td><td>${num(v.total)}</td><td>${v.avgMonthly}</td><td>${trendCell(v.trend)}</td>
                <td>${v.stock.stock != null ? num(v.stock.stock) : '—'}</td><td>${stockBadge(v.stock)}</td></tr>`).join('') : '<tr><td colspan="6" class="ai-none">No vaccine doses recorded in this period.</td></tr>'}
            </tbody></table></div>
            <p class="ai-evid">Doses come from Immunization records. Stock is shown only when exactly one active vaccine in the inventory matches the vaccine name.${vi.length ? ' Vaccines dispensed directly from inventory are listed below.' : ''}</p>
            ${vi.length ? `<div class="table-wrap"><table>${THEAD}<tbody>${rowsFor(vi, 'vaccines dispensed from inventory')}</tbody></table></div>` : ''}</div></div>
        <div class="card ai-gap"><div class="card-body"><div class="ai-head"><div class="card-title">Other health supplies</div><span class="ai-tag rec">Recorded Data</span></div>
            <div class="table-wrap"><table>${THEAD}<tbody>${rowsFor(sup, 'dispensed supplies')}</tbody></table></div></div></div>
        <div class="ai-grid-3 ai-gap">
            <div class="card"><div class="card-body"><div class="card-title">Increasing usage</div><ul class="ai-ul">${li(S.increasing, m => `<li>${esc(m.name)} <span>${m.trend.prior} → ${m.trend.recent} / month</span></li>`)}</ul></div></div>
            <div class="card"><div class="card-body"><div class="card-title">Decreasing usage</div><ul class="ai-ul">${li(S.decreasing, m => `<li>${esc(m.name)} <span>${m.trend.prior} → ${m.trend.recent} / month</span></li>`)}</ul></div></div>
            <div class="card"><div class="card-body"><div class="card-title">Consistently high demand</div><ul class="ai-ul">${li(S.consistent, m => `<li>${esc(m.name)} <span>${num(m.total)} ${esc(m.unit)}</span></li>`)}</ul></div></div></div>
        <p class="ai-evid">Quantities count every dispensed unit in the selected dates. Trends compare the last 3 complete months with the 3 before them, and need ${RE.CFG.minMonthsTrend}+ months and ${RE.CFG.minUnitsTrend}+ units. Stock is “Low” when it is at or below the reorder level or covers less than one month of recent average usage.</p>`;
    }

    /* ============ SUB 2: Seasonal resource monitoring ============ */
    const ORDER = ['respiratory', 'vector', 'heat'];
    const CONCERN = { respiratory: 'Respiratory illnesses', vector: 'Vector-borne diseases', heat: 'Heat-related illnesses' };
    function resRows(arr, tag) {
        if (!arr.length) return '';
        return `<div class="ai-sub">${esc(tag.title)}</div><ul class="ai-ul">${arr.slice(0, 6).map(x => `<li>${esc(x.name)} <span>${tag.fmt(x)}</span></li>`).join('')}</ul>`;
    }
    function subSeasonal() {
        const r = R.result;
        const cardHTML = c => {
            const h = c.health, T = { current: 'This period is under way now', approaching: `Starts in about ${c.timing.days} days`, later: 'Not approaching yet' }[c.timing.state];
            return `<div class="card"><div class="card-body">
                <div class="ai-head"><div><div class="card-title">Seasonal Resource Monitoring — ${esc(c.window.range)}</div><div class="sec-sub">${esc(c.window.label)} • ${esc(T)}</div></div><span class="ai-status st-${h.status}">${esc(HSTATUS[h.status])}</span></div>
                <div class="ai-sub">Possible health concern</div><p class="res-p">${esc(CONCERN[c.key])}${c.diseases.length ? ` — recorded as ${esc(c.diseases.join(', '))}` : ''}</p>
                <div class="ai-sub">Observed health pattern</div><p class="res-p">${esc(h.detail)}</p>
                ${resRows(c.bucket.supported, { title: 'Frequently used resources during similar periods (supported by records)', fmt: x => `${x.avgIn} vs ${x.avgOut} ${esc(x.unit)}/month • ${x.rx} linked prescriptions` })}
                ${resRows(c.bucket.linked, { title: 'Dispensed for these cases, but no seasonal rise in usage observed', fmt: x => `${x.rx} linked prescriptions` })}
                ${resRows(c.bucket.elevated, { title: 'Higher usage in these months, but no recorded prescription link to these cases', fmt: x => `${x.avgIn} vs ${x.avgOut} ${esc(x.unit)}/month` })}
                <div class="ai-insight lvl-${c.bucket.supported.length ? 'moderate' : 'routine'}"><div class="ai-insight-head"><span class="ai-lvl">AI INSIGHT</span><span class="ai-src">Generated from recorded data</span></div><p>${esc(c.insight)}</p></div>
                ${c.bucket.supported.length ? `<div style="height:220px;position:relative;margin-top:14px"><canvas id="resSea_${c.key}"></canvas></div><p class="ai-evid">Darker columns fall inside ${esc(c.window.range)}. Shown for ${esc(c.bucket.supported[0].name)}.</p>` : ''}
            </div></div>`;
        };
        return `<div class="ai-grid-2">${ORDER.map(k => cardHTML(r.cards.find(c => c.key === k))).join('')}</div>
        <div class="card ai-gap"><div class="card-body"><div class="ai-head"><div class="card-title">Resource Demand Monitoring</div><span class="ai-tag obs">Observed Pattern</span></div>
            <div class="table-wrap"><table><thead><tr><th>Period</th><th>Health pattern</th><th>Resource pattern</th><th>Monitoring</th></tr></thead><tbody>
            ${r.demand.map(d => `<tr><td>${esc(d.period)}<br><span class="ai-none">${esc(d.concern)}</span></td><td>${esc(d.health)}</td><td>${esc(d.resource)}</td><td>${esc(d.monitoring)}</td></tr>`).join('')}</tbody></table></div>
            <p class="ai-evid">A resource is listed only when the records support the relationship: dispensed prescriptions linked to the recorded cases (at least ${RE.CFG.minRxLink}) <b>and</b> higher dispensing in the window months. A common disease alone does not make a medicine necessary. These patterns describe past records and are not predictions.</p></div></div>`;
    }
    function mountSeasonal() {
        R.result.cards.forEach(c => { if (c.bucket.supported.length) mk('sea_' + c.key, 'resSea_' + c.key, seasonConfig(itemById(c.bucket.supported[0].id), c.window.months)); });
    }

    /* ============ SUB 3: Usage graphs ============ */
    function subGraphs() {
        const r = R.result, m = itemById(R.item), A = m && r.assoc.get(m.id);
        const dOpts = r.diseases.map(d => `<option value="${esc(d.name)}" ${d.name === R.disease ? 'selected' : ''}>${esc(d.name)}</option>`).join('');
        const d = diseaseByName(R.disease);
        const corr = m && d && A ? (r.sufficiency.months >= RE.CFG.corrMinMonths ? RE.pearson(m.series, d.series) : null) : null;
        const link = m && d ? (r.assoc.get(m.id).diseases.find(x => x.disease === d.name) || { rx: 0 }) : { rx: 0 };
        return `<div class="ai-grid-2">
            <div class="card"><div class="card-body"><div class="ai-head"><div><div class="card-title">Monthly usage by category</div><div class="sec-sub">Dispensed quantity • ${esc(rangeText())}</div></div><span class="ai-tag rec">Recorded Data</span></div>
                <div style="height:280px;position:relative"><canvas id="resCat"></canvas></div></div></div>
            <div class="card"><div class="card-body"><div class="ai-head"><div><div class="card-title">Frequently distributed items</div><div class="sec-sub">Total dispensed • ${esc(rangeText())}</div></div><span class="ai-tag rec">Recorded Data</span></div>
                <div style="height:280px;position:relative"><canvas id="resTop"></canvas></div></div></div></div>
        <div class="card ai-gap"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">Item usage trend</div><div class="sec-sub">Actual monthly quantity dispensed. Orange points mark months where usage rose from the month before.</div></div>
                <select class="ai-select" id="resItemSel" aria-label="Select item">${r.used.map(x => `<option value="${x.id}" ${String(x.id) === String(R.item) ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></div>
            ${m ? `<div style="height:300px;position:relative"><canvas id="resItem"></canvas></div>` : empty('No dispensed items in this period.')}</div></div>
        <div class="ai-grid-2 ai-gap">
            <div class="card"><div class="card-body"><div class="ai-head"><div><div class="card-title">Current stock vs historical usage</div><div class="sec-sub">Top dispensed items</div></div></div>
                <div style="height:320px;position:relative"><canvas id="resStock"></canvas></div></div></div>
            <div class="card"><div class="card-body"><div class="ai-head"><div><div class="card-title">Usage by calendar month</div><div class="sec-sub">${m ? esc(m.name) + ' — average per month' : ''}</div></div></div>
                <div style="height:320px;position:relative"><canvas id="resCal"></canvas></div><p class="ai-evid">Empty months have no complete record in the selected period.</p></div></div></div>
        <div class="card ai-gap"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">Medicine usage and disease trends</div><div class="sec-sub">Dispensed quantity beside recorded cases of one disease</div></div>
                <select class="ai-select" id="resDisSel" aria-label="Select disease">${dOpts || '<option>No diseases recorded</option>'}</select></div>
            ${m && d ? `<div style="height:300px;position:relative"><canvas id="resAssoc"></canvas></div>
            <p class="ai-evid">${link.rx >= RE.CFG.minRxLink ? `${link.rx} dispensed prescriptions of ${esc(m.name)} were recorded with ${esc(d.name)} cases.` : `No recorded prescription links ${esc(m.name)} to ${esc(d.name)} (fewer than ${RE.CFG.minRxLink}).`}
            ${corr !== null ? ` Month-to-month, the two lines ${corr >= RE.CFG.corrMin && link.rx >= RE.CFG.minRxLink ? 'moved together' : 'did not move clearly together'} (correlation ${corr.toFixed(2)}). This shows co-movement in records, not that one causes the other.` : ''}</p>` : empty('Select an item and a disease with recorded data.')}</div></div>
        <p class="ai-evid">Graphs use complete calendar months only, so a partial first or last month does not look like a false drop.</p>`;
    }
    function mountGraphs() {
        const r = R.result, m = itemById(R.item), d = diseaseByName(R.disease);
        mk('cat', 'resCat', catConfig()); mk('top', 'resTop', topConfig()); mk('stock', 'resStock', stockConfig());
        if (m) { mk('item', 'resItem', itemConfig(m)); mk('cal', 'resCal', seasonConfig(m, [])); }
        if (m && d) mk('assoc', 'resAssoc', assocConfig(m, d));
    }

    /* ============ SUB 4: Monitoring alerts ============ */
    function subAlerts() {
        const r = R.result;
        if (!r.alerts.length) return `<div class="card"><div class="card-body">${empty('No resource currently combines several monitoring signals (high usage, increasing usage, low stock, repeated distribution, seasonal pattern, rising disease trend). Continue recording dispensing and stock updates.')}</div></div>`;
        return `<p class="ai-evid" style="margin-top:0">An item appears here only when at least two signals from the records occur together. “Priority” means stock is low or out and other signals are present.</p>` +
            r.alerts.map(a => `<div class="card ai-gap"><div class="card-body">
                <div class="ai-head"><div><div class="card-title">RESOURCE REQUIRING MONITORING</div><div class="sec-sub">${esc(a.category)} • ${esc(r.period)}</div></div><span class="res-level lv-${a.level}">${a.level === 'priority' ? 'Priority monitoring' : 'Watch'}</span></div>
                <div class="res-kpis"><div><span>${esc(a.category)}</span><b>${esc(a.name)}</b></div>
                    <div><span>Usage</span><b>${a.signals.includes('High usage') ? 'High' : 'Recorded'}</b></div>
                    <div><span>Trend</span><b>${TREND_TXT[a.m.trend.status]}</b></div>
                    <div><span>Current stock</span><b>${num(a.m.stock.stock)} ${esc(a.unit)}</b>${stockBadge(a.m.stock)}</div></div>
                <div class="res-chips">${a.signals.map(s => `<span class="res-chip">${esc(s)}</span>`).join('')}</div>
                <div class="ai-insight lvl-${a.level === 'priority' ? 'high' : 'moderate'}"><div class="ai-insight-head"><span class="ai-lvl">AI INSIGHT</span><span class="ai-src">Generated from recorded data</span></div><p>${esc(a.insight)}</p></div></div></div>`).join('');
    }

    /* ---------- events ---------- */
    document.addEventListener('click', e => {
        const t = e.target.closest('[data-ressub]');
        if (t && R.result) { R.sub = t.dataset.ressub; document.querySelectorAll('[data-ressub]').forEach(b => b.classList.toggle('active', b === t)); renderSub(); }
    });
    document.addEventListener('change', e => {
        if (!R.result) return;
        if (e.target.id === 'resItemSel') { R.item = e.target.value; const a = R.result.assoc.get(Number(R.item)); if (a && a.topDisease) R.disease = a.topDisease; renderSub(); }
        if (e.target.id === 'resDisSel') { R.disease = e.target.value; renderSub(); }
    });

    /* ============ Export / print ============ */
    function printReport() {
        const r = R.result; if (!r) { showToast('Open the Resource Allocation tab first.', 'error'); return; }
        const user = (typeof getCurrentUser === 'function' ? getCurrentUser() : {}) || {};
        const S = r.summary, top = S.topMedicine, m0 = itemById(R.item) || top || r.used[0], d0 = diseaseByName(R.disease);
        const catImg = r.used.length ? imgOf(catConfig()) : '', topImg = r.used.length ? imgOf(topConfig(), 900, 300) : '';
        const itemImg = m0 ? imgOf(itemConfig(m0)) : '', stockImg = r.used.length ? imgOf(stockConfig(), 900, 300) : '';
        const assocImg = m0 && d0 ? imgOf(assocConfig(m0, d0)) : '';
        const trow = (arr, f) => arr.length ? arr.slice(0, 15).map(f).join('') : '';
        const usedRow = m => `<tr><td>${esc(m.name)}</td><td>${esc(m.category)}</td><td>${num(m.total)} ${esc(m.unit)}</td><td>${m.avgMonthly}</td><td>${TREND_TXT[m.trend.status]}${m.trend.pct != null && m.trend.status !== 'stable' && m.trend.status !== 'insufficient' ? ` (${m.trend.pct > 0 ? '+' : ''}${m.trend.pct}%)` : ''}</td><td>${num(m.stock.stock)}</td><td>${esc(m.stock.label)}</td></tr>`;
        const html = `
        <h1>Resource Allocation Analysis</h1>
        <div class="p-meta"><div><b>Health center:</b> ${esc($('barangayLabel')?.textContent || '')}</div><div><b>Date range:</b> ${esc(rangeText())}</div>
            <div><b>Generated:</b> ${esc(new Date().toLocaleString('en-PH', { dateStyle: 'long', timeStyle: 'short' }))}${user.name ? ' by ' + esc(user.name) : ''}</div></div>
        <p class="p-note">Decision-support only. This report describes recorded inventory, dispensing, and disease data. It does not purchase, order, distribute, or change inventory. <b>Recorded Data</b> = quantities and stock stored in the system. <b>Observed Pattern</b> = a pattern found in historical records.</p>

        <h2>1. Frequently used resources</h2>
        ${top ? `<div class="p-ins"><h3>Most frequently used medicine — ${esc(top.name)}</h3><p><b>Usage trend:</b> ${TREND_TXT[top.trend.status]} • <b>Current stock:</b> ${num(top.stock.stock)} ${esc(top.unit)} (${esc(top.stock.label)}) • <b>Usage period:</b> ${esc(r.period)}</p><p><b>Insight:</b> ${esc(top.insight)}</p></div>` : '<p>No medicines were dispensed in this period.</p>'}
        <table><thead><tr><th>Item</th><th>Category</th><th>Quantity used</th><th>Avg / month</th><th>Usage trend</th><th>Current stock</th><th>Stock status</th></tr></thead><tbody>${trow(r.used, usedRow) || '<tr><td colspan="7">No dispensing recorded.</td></tr>'}</tbody></table>
        <h3>Vaccines administered (Immunization records)</h3>
        <table><thead><tr><th>Vaccine</th><th>Doses</th><th>Avg / month</th><th>Trend</th><th>Inventory stock</th></tr></thead><tbody>${r.vaccines.length ? r.vaccines.slice(0, 12).map(v => `<tr><td>${esc(v.name)}</td><td>${num(v.total)}</td><td>${v.avgMonthly}</td><td>${TREND_TXT[v.trend.status]}</td><td>${v.stock.stock != null ? num(v.stock.stock) + ' (' + esc(v.stock.label) + ')' : 'Not linked to inventory'}</td></tr>`).join('') : '<tr><td colspan="5">No vaccine doses recorded.</td></tr>'}</tbody></table>
        <ul><li><b>Increasing usage:</b> ${S.increasing.length ? S.increasing.slice(0, 8).map(m => esc(m.name)).join(', ') : 'none identified'}</li>
            <li><b>Decreasing usage:</b> ${S.decreasing.length ? S.decreasing.slice(0, 8).map(m => esc(m.name)).join(', ') : 'none identified'}</li>
            <li><b>Consistently high demand:</b> ${S.consistent.length ? S.consistent.slice(0, 8).map(m => esc(m.name)).join(', ') : 'none identified'}</li></ul>

        <h2>2. Usage trends</h2>
        ${catImg ? `<img src="${catImg}" alt="Monthly usage by category"><img src="${topImg}" alt="Frequently distributed items">` : '<p>No dispensing recorded.</p>'}
        ${itemImg ? `<h3>${esc(m0.name)} usage trend</h3><img src="${itemImg}" alt="Item usage trend">` : ''}

        <h2>3. Current stock information</h2>
        ${stockImg ? `<img src="${stockImg}" alt="Current stock vs usage">` : ''}
        <table><thead><tr><th>Item</th><th>Current stock</th><th>Reorder level</th><th>Months of supply at recent usage</th><th>Status</th></tr></thead><tbody>${trow(r.used.slice(0, 15), m => `<tr><td>${esc(m.name)}</td><td>${num(m.stock.stock)}</td><td>${num(m.stock.reorder)}</td><td>${m.stock.coverage == null ? '—' : m.stock.coverage}</td><td>${esc(m.stock.label)} — ${esc(m.stock.why)}</td></tr>`) || '<tr><td colspan="5">No data.</td></tr>'}</tbody></table>

        <h2>4. Seasonal resource monitoring</h2>
        ${r.cards.map(c => `<div class="p-ins"><h3>${esc(c.window.range)} — ${esc(CONCERN[c.key])} (${esc(HSTATUS[c.health.status])})</h3>
            <p><b>Observed health pattern:</b> ${esc(c.health.detail)}</p>
            ${c.bucket.supported.length ? `<p><b>Resources supported by records:</b> ${c.bucket.supported.slice(0, 6).map(x => `${esc(x.name)} (${x.avgIn} vs ${x.avgOut} ${esc(x.unit)}/month; ${x.rx} linked prescriptions)`).join('; ')}</p>` : '<p><b>Resources supported by records:</b> none identified.</p>'}
            ${c.bucket.elevated.length ? `<p><b>Higher usage without a recorded link to these cases:</b> ${c.bucket.elevated.slice(0, 6).map(x => esc(x.name)).join(', ')}</p>` : ''}
            <p><b>Insight:</b> ${esc(c.insight)}</p></div>`).join('')}

        <h2>5. Resource demand patterns</h2>
        <table><thead><tr><th>Period</th><th>Health pattern</th><th>Resource pattern</th><th>Monitoring</th></tr></thead><tbody>${r.demand.map(d => `<tr><td>${esc(d.period)} (${esc(d.concern)})</td><td>${esc(d.health)}</td><td>${esc(d.resource)}</td><td>${esc(d.monitoring)}</td></tr>`).join('')}</tbody></table>

        <h2>6. Relevant disease trends</h2>
        <table><thead><tr><th>Disease</th><th>Recorded cases</th><th>Recent trend (last 3 vs prior 3 months)</th></tr></thead><tbody>${r.diseases.slice(0, 12).map(d => `<tr><td>${esc(d.name)}</td><td>${num(d.total)}</td><td>${TREND_TXT[d.trend.status]}${d.trend.pct != null && (d.trend.status === 'increasing' || d.trend.status === 'decreasing') ? ` (${d.trend.pct > 0 ? '+' : ''}${d.trend.pct}%)` : ''}</td></tr>`).join('') || '<tr><td colspan="3">No cases recorded.</td></tr>'}</tbody></table>
        ${assocImg ? `<h3>${esc(m0.name)} usage beside ${esc(d0.name)} cases</h3><img src="${assocImg}" alt="Usage and cases">` : ''}

        <h2>7. AI-generated insights and monitoring considerations</h2>
        ${r.alerts.length ? r.alerts.slice(0, 8).map(a => `<div class="p-ins"><h3>RESOURCE REQUIRING MONITORING — ${esc(a.name)} (${a.level === 'priority' ? 'Priority' : 'Watch'})</h3>
            <p><b>Signals:</b> ${a.signals.map(esc).join(', ')}</p><p>${esc(a.insight)}</p></div>`).join('') : '<p>No resource currently combines several monitoring signals.</p>'}

        <h2>8. Data limitations</h2>
        <ul><li><b>Data sufficiency:</b> ${esc(LEVEL_TEXT[r.sufficiency.level])}. ${esc(r.sufficiency.message)}</li>${r.sufficiency.notes.map(n => `<li>${esc(n)}</li>`).join('')}
        <li>Usage counts only dispensed prescriptions. Vaccine doses come from Immunization records and are matched to inventory stock only when the name matches exactly one item.</li>
        <li>A medicine is linked to a disease only through dispensed prescriptions recorded with that disease (at least ${RE.CFG.minRxLink}). A common disease alone does not make a medicine necessary.</li>
        <li>No temperature or weather data is integrated. Warmer-month patterns describe recorded cases only and do not state that any month will be hotter.</li>
        <li>Patterns describe past records, not predictions. This analysis does not purchase, order, distribute, or change inventory; final decisions remain with authorized health workers and administrators.</li></ul>
        <div class="p-foot">Report generated ${esc(new Date().toLocaleDateString('en-PH', { dateStyle: 'long' }))} • ${esc(rangeText())}</div>`;
        let el = $('aiPrintReport'); if (!el) { el = document.createElement('div'); el.id = 'aiPrintReport'; document.body.appendChild(el); }
        el.innerHTML = html;
        document.body.classList.add('ai-printing');
        const done = () => { document.body.classList.remove('ai-printing'); window.removeEventListener('afterprint', done); };
        window.addEventListener('afterprint', done);
        setTimeout(() => window.print(), 200);
    }
})();
