/* ============================================================
   AI INSIGHTS — UI (admin only)
   Depends on: ai-insights-engine.js, Chart.js, DateRangePicker,
   and the dashboard globals apiGet / apiPost / showToast / formatDisplayDate.
   The AI provider key lives on the server (.env); nothing secret is here.
   ============================================================ */
(function () {
    'use strict';
    const E = window.AIEngine;
    const $ = id => document.getElementById(id);
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const NAVY = '#0b1f4b', MUTED = '#8a97b3', LIGHT = '#dce5f5';
    const PAL = [NAVY, '#2255c4', '#14b8a6', '#f59e0b', MUTED];
    const S = { tab: 'trends', result: null, disease: '__top', area: '__all', cat: 'all', charts: {}, narrativeSummary: '', pickerReady: false, token: 0 };

    // Para sa Resource Allocation module (resource-insights.js)
    window.AIInsightsCtx = { range: () => ({ start: $('aiStartDate')?.value, end: $('aiEndDate')?.value }) };

    const LEVEL_TEXT = { insufficient: 'Insufficient data', limited: 'Limited data', moderate: 'Moderate data', good: 'Sufficient data' };
    const CONF_TEXT = { high: 'High confidence', moderate: 'Moderate confidence', low: 'Low confidence', insufficient: 'Insufficient data' };
    const CAT_REF = {
        respiratory: 'Reference: respiratory-related illnesses such as influenza and other respiratory infections.',
        heat: 'Reference: heat-related conditions such as heat exhaustion and heat stroke, particularly among older adults.',
        vector: 'Reference: diseases that may recur during rainy periods, such as dengue.'
    };

    /* ---------- entry points ---------- */
    window.loadAIInsights = async function () {
        if (sessionStorage.getItem('user_role') !== 'admin') { showToast('AI Insights is available to administrators only.', 'error'); showSection('dashboard'); return; }
        ensurePicker();
        const start = $('aiStartDate')?.value, end = $('aiEndDate')?.value;
        if (!start || !end) return;
        if (start > end) { showToast('Start date must be before the end date.', 'error'); return; }
        const root = $('aiRoot'), tk = ++S.token;
        root.innerHTML = '<div class="ai-empty">Analyzing recorded cases…</div>';
        let data = null;
        try { data = await apiGet(`/api/ai-insights/data?start_date=${start}&end_date=${end}`); } catch (_) { }
        if (tk !== S.token) return;
        if (!data || !Array.isArray(data.rows)) {
            root.innerHTML = '<div class="ai-empty">AI Insights data could not be loaded. Confirm you are signed in as an administrator and that the <code>/api/ai-insights/data</code> endpoint is available.</div>';
            S.result = null; return;
        }
        S.result = E.analyze(data.rows, { start, end });
        S.disease = S.result.monitored[0]?.name || '__top';
        S.area = '__all'; S.cat = 'all'; S.narrativeSummary = '';
        renderShell();
        fetchNarrative(tk);
    };

    function ensurePicker() {
        if (S.pickerReady) return;
        const rangeEl = $('aiDateRange'); if (!rangeEl || typeof DateRangePicker === 'undefined') return;
        const today = new Date(), from = new Date(today.getFullYear(), today.getMonth() - 24, 1);
        const picker = new DateRangePicker(rangeEl, { format: 'yyyy-mm-dd', autohide: true, todayHighlight: true, maxDate: today,
            prevArrow: '<i class="fa-solid fa-chevron-left"></i>', nextArrow: '<i class="fa-solid fa-chevron-right"></i>' });
        picker.setDates(from, today);
        let t; const on = () => { clearTimeout(t); t = setTimeout(window.loadAIInsights, 250); };
        $('aiStartDate').addEventListener('changeDate', on); $('aiEndDate').addEventListener('changeDate', on);
        S.pickerReady = true;
    }

    /* ---------- optional server-side AI wording (key stays in .env on the server) ---------- */
    async function fetchNarrative(tk) {
        const r = S.result; if (!r || !r.monitored.length) return;
        try {
            const res = await apiPost('/api/ai-insights/narrative', {
                range: r.range, data_level: r.sufficiency.level, months: r.sufficiency.months,
                findings: r.monitored.slice(0, 6).map(d => ({ id: d.name, level: d.monitoring.level, pattern: d.pattern, basis: d.basis, why: d.insight.why, monitor: d.insight.monitor, period: d.insight.period }))
            });
            if (tk !== S.token || !res) return;
            if (res.items) r.diseases.forEach(d => { const x = res.items[d.name]; if (x && x.why && x.monitor && x.period) Object.assign(d.insight, { why: String(x.why), monitor: String(x.monitor), period: String(x.period), source: 'ai' }); });
            if (res.summary) S.narrativeSummary = String(res.summary);
            renderShell();
        } catch (_) { /* rule-based wording stays */ }
    }

    /* ---------- shell ---------- */
    function renderShell() {
        const r = S.result, s = r.sufficiency;
        const tabs = [['trends', 'Disease Trends'], ['seasonal', 'Seasonal Monitoring'], ['area', 'Area Monitoring'], ['forecast', 'Forecasting'], ['resources', 'Resource Allocation']];
        $('aiRoot').innerHTML = `
        <div class="ai-banner lvl-${s.level}">
            <div class="ai-banner-main">
                <span class="ai-pill">${LEVEL_TEXT[s.level]}</span>
                <span>${esc(s.message)}</span>
            </div>
            <div class="ai-banner-stats">${r.grandTotal.toLocaleString()} cases • ${r.diseases.length} diseases • ${s.months} complete months</div>
            ${s.notes.map(n => `<div class="ai-note">${esc(n)}</div>`).join('')}
            ${S.narrativeSummary ? `<div class="ai-summary"><span class="ai-tag fc">AI-generated summary</span> ${esc(S.narrativeSummary)}</div>` : ''}
        </div>
        <div class="ai-legend">
            <span><span class="ai-tag rec">Recorded Data</span> actual cases stored in the system</span>
            <span><span class="ai-tag obs">Observed Trend</span> pattern found in historical records</span>
            <span><span class="ai-tag fc">AI Forecast</span> possible future pattern based on available data</span>
        </div>
        <div class="chart-tabbar"><div class="chart-tabs">${tabs.map(([k, l]) => `<button class="chart-tab ${S.tab === k ? 'active' : ''}" data-aitab="${k}">${l}</button>`).join('')}</div></div>
        <div id="aiPanels"></div>
        <div class="ai-disclaimer">AI Insights is decision-support only. Findings describe patterns in recorded data; interpretation and health decisions remain with authorized health workers.</div>`;
        renderTab();
    }

    function renderTab() {
        Object.values(S.charts).forEach(c => c.destroy()); S.charts = {};
        const p = $('aiPanels'); if (!p) return;
        $('aiRoot').classList.toggle('ai-res-mode', S.tab === 'resources');   // itago ang disease banner sa Resource tab
        p.innerHTML = { trends: tabTrends, seasonal: tabSeasonal, area: tabArea, forecast: tabForecast,
            resources: () => window.ResourceUI ? window.ResourceUI.html() : emptyMsg('Resource Allocation module is not loaded.') }[S.tab]();
        ({ trends: mountTrends, seasonal: mountSeasonal, area: mountArea, forecast: () => { },
            resources: () => { if (window.ResourceUI) window.ResourceUI.mount(); } })[S.tab]();
    }

    /* ---------- chart builders (shared by screen and print) ---------- */
    const baseOpts = () => ({ responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 }, filter: i => !i.text.startsWith('_') } }, datalabels: { display: false }, tooltip: { filter: i => !i.dataset.label.startsWith('_') } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } } });

    function trendConfig(d) {
        const r = S.result, n = r.months.length;
        if (!d) return { type: 'line', data: { labels: r.monthLabels, datasets: r.diseases.slice(0, 5).map((x, i) => ({ label: x.name, data: x.series, borderColor: PAL[i], backgroundColor: PAL[i], borderWidth: 2, pointRadius: 2, tension: .25 })) }, options: baseOpts() };
        const fc = d.forecast.status === 'ok' ? d.forecast.points : [], pad = Array(fc.length).fill(null);
        const labels = [...r.monthLabels, ...fc.map(p => p.label)];
        const ma = d.series.map((_, i) => i < 2 ? null : Math.round(d.series.slice(i - 2, i + 1).reduce((a, b) => a + b, 0) / 3 * 10) / 10);
        const join = arr => [...Array(n - 1).fill(null), d.series[n - 1], ...arr];
        const ymax = Math.ceil(Math.max(...d.series, ...fc.map(p => p.high)) * 1.1) || 1;
        const peak = d.seasonal.status === 'supported' ? d.seasonal.peakMonths : [];
        const monthNo = i => i < n ? Number(r.months[i].slice(5)) : Number(fc[i - n].ym.slice(5));
        const datasets = [
            { type: 'bar', label: 'Months with historically higher activity', data: labels.map((_, i) => peak.includes(monthNo(i)) ? ymax : null), backgroundColor: '#dce5f588', borderWidth: 0, barPercentage: 1, categoryPercentage: 1, order: 9 },
            { label: 'Recorded cases', data: [...d.series, ...pad], borderColor: NAVY, backgroundColor: NAVY, borderWidth: 2, pointRadius: 3, tension: .2, order: 3 },
            { label: 'Observed trend (3-month average)', data: [...ma, ...pad], borderColor: MUTED, borderDash: [6, 4], borderWidth: 2, pointRadius: 0, tension: .3, order: 4 }
        ];
        if (fc.length) datasets.push(
            { label: '_low', data: join(fc.map(p => p.low)), borderWidth: 0, pointRadius: 0, order: 6 },
            { label: '_high', data: join(fc.map(p => p.high)), borderWidth: 0, pointRadius: 0, backgroundColor: '#2255c41f', fill: '-1', order: 6 },
            { label: 'AI forecast (possible range shaded)', data: join(fc.map(p => p.value)), borderColor: '#2255c4', borderDash: [2, 4], borderWidth: 2, pointRadius: 3, pointStyle: 'rectRot', tension: .2, order: 2 });
        const o = baseOpts(); o.scales.y.suggestedMax = ymax;
        return { type: 'line', data: { labels, datasets }, options: o };
    }

    function seasonalConfig(moy, hi) {
        return { type: 'bar', data: { labels: E.MN, datasets: [{ label: 'Average cases per month', data: moy, backgroundColor: E.MN.map((_, i) => hi.includes(i + 1) ? NAVY : '#b8c4de'), borderRadius: 4 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, datalabels: { anchor: 'end', align: 'top', color: NAVY, font: { size: 10 }, formatter: v => v > 0 ? v : '' } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grace: '15%' } } } };
    }

    function areaConfig(A) {
        const a = A.areas.slice(0, 10);
        return { type: 'bar', data: { labels: a.map(x => x.name), datasets: [{ label: 'Recorded cases', data: a.map(x => x.cases), backgroundColor: a.map((_, i) => i === 0 ? NAVY : '#b8c4de'), borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false }, datalabels: { anchor: 'end', align: 'end', color: NAVY, font: { size: 10 }, formatter: v => v }, tooltip: { callbacks: { afterLabel: c => `${a[c.dataIndex].share}% of recorded cases` } } },
                scales: { x: { beginAtZero: true, grace: '12%', ticks: { precision: 0 } }, y: { grid: { display: false } } } } };
    }

    function mk(key, id, cfg) { const el = $(id); if (el) S.charts[key] = new Chart(el, cfg); }
    function imgOf(cfg, w = 900, h = 320) {
        const c = document.createElement('canvas'); c.width = w; c.height = h;
        cfg.options = Object.assign({}, cfg.options, { responsive: false, animation: false, devicePixelRatio: 1 });
        cfg.plugins = [{ id: 'bg', beforeDraw(ch) { const x = ch.ctx; x.save(); x.globalCompositeOperation = 'destination-over'; x.fillStyle = '#fff'; x.fillRect(0, 0, ch.width, ch.height); x.restore(); } }];
        const ch = new Chart(c, cfg); const u = ch.toBase64Image(); ch.destroy(); return u;
    }

    /* ---------- shared fragments ---------- */
    const diseaseOptions = (sel, extra) => `${extra || ''}${S.result.diseases.map(d => `<option value="${esc(d.name)}" ${d.name === sel ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}`;
    const rangeText = () => `${formatDisplayDate(S.result.range.start)} – ${formatDisplayDate(S.result.range.end)}`;

    function insightHTML(d) {
        const i = d.insight;
        return `<div class="ai-insight lvl-${i.level}">
            <div class="ai-insight-head"><span class="ai-lvl">${esc(i.title)}</span>
                <span class="ai-src">${i.source === 'ai' ? 'AI-generated from recorded data' : 'Generated from recorded data'}</span></div>
            <div class="ai-sub">Why it was identified</div><p>${esc(i.why)}</p>
            <div class="ai-sub">What to monitor</div><p>${esc(i.monitor)}</p>
            <div class="ai-sub">Recommended monitoring period</div><p>${esc(i.period)}</p></div>`;
    }
    const emptyMsg = t => `<div class="ai-empty">${t}</div>`;
    const noData = () => !S.result.diseases.length ? emptyMsg('No disease cases were recorded in the selected period. Continue recording cases to enable analysis.') : '';

    /* ---------- TAB 1: Disease trends ---------- */
    function tabTrends() {
        const r = S.result; if (!r.diseases.length) return noData();
        const d = r.diseases.find(x => x.name === S.disease);
        const li = (arr, f) => arr.length ? arr.map(f).join('') : '<li class="ai-none">None identified in this period</li>';
        const ins = d ? insightHTML(d) : (r.monitored.slice(0, 3).map(insightHTML).join('') || emptyMsg('No disease currently meets the criteria for increased monitoring.'));
        return `<div class="card"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">Disease Trend Analysis</div><div class="sec-sub">Monthly recorded cases • ${esc(rangeText())}</div></div>
                <select class="ai-select" id="aiDisSel" aria-label="Select disease">${diseaseOptions(S.disease, '<option value="__top">Top 5 diseases (recorded cases only)</option>')}</select></div>
            <div style="height:340px;position:relative"><canvas id="aiTrendChart"></canvas></div>
            <div class="chart-footnote"><i class="fa-regular fa-circle-question"></i><span>Solid line = recorded data. Dashed line = observed trend. Dotted line and shaded band = AI forecast, shown only when enough history exists. Light columns mark months with historically higher activity that recurred across years.</span></div>
            ${ins}</div></div>
        <div class="card ai-gap"><div class="card-body"><div class="ai-head"><div class="card-title">Disease pattern summary</div><span class="ai-tag obs">Observed Trend</span></div>
            <div class="ai-rows">${r.diseases.map((x, i) => `<button class="ai-row" data-aidis="${i}">
                <span class="ai-row-name">${esc(x.name)}</span><span class="ai-pat pat-${x.metrics.pattern}">${esc(x.pattern)}</span>
                <span class="ai-row-basis">${esc(x.basis)}</span></button>`).join('')}</div></div></div>
        <div class="ai-grid-3 ai-gap">
            <div class="card"><div class="card-body"><div class="card-title">Increasing diseases</div><ul class="ai-ul">${li(r.summary.increasing, x => `<li>${esc(x.name)} <span>${x.metrics.changePct != null ? (x.metrics.changePct > 0 ? '+' : '') + x.metrics.changePct + '%' : 'new activity'}</span></li>`)}</ul></div></div>
            <div class="card"><div class="card-body"><div class="card-title">Decreasing diseases</div><ul class="ai-ul">${li(r.summary.decreasing, x => `<li>${esc(x.name)} <span>${x.metrics.changePct}%</span></li>`)}</ul></div></div>
            <div class="card"><div class="card-body"><div class="card-title">Frequently recorded diseases</div><ul class="ai-ul">${li(r.summary.frequent, x => `<li>${esc(x.name)} <span>${x.total} cases • ${x.share}%</span></li>`)}</ul></div></div>
        </div>`;
    }
    function mountTrends() {
        if (!S.result.diseases.length) return;
        const d = S.result.diseases.find(x => x.name === S.disease);
        mk('trend', 'aiTrendChart', trendConfig(d));
    }

    /* ---------- TAB 2: Seasonal ---------- */
    function tabSeasonal() {
        const r = S.result, sea = r.seasonal; if (!r.diseases.length) return noData();
        const cats = [['all', 'All recorded diseases'], ...sea.cards.map(c => [c.key, c.label])];
        const h = r.heat;
        return `<div class="card"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">Recorded cases by calendar month</div><div class="sec-sub">Average cases per month across the selected period • ${esc(rangeText())}</div></div>
                <select class="ai-select" id="aiCatSel" aria-label="Select category">${cats.map(([k, l]) => `<option value="${k}" ${k === S.cat ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
            <div style="height:280px;position:relative"><canvas id="aiSeasonChart"></canvas></div>
            <div class="chart-footnote"><i class="fa-regular fa-circle-question"></i><span>Darker columns fall inside the reference window for the selected category. Empty months have no records in the selected range. <span class="ai-tag obs">Observed Trend</span></span></div></div></div>
        <div class="ai-grid-3 ai-gap">${['cooler', 'warmer', 'rainy'].map(k => { const c = sea.cards.find(x => x.season === k), w = c.window; return `
            <div class="card"><div class="card-body">
                <div class="card-title">${esc(w.label)}</div><div class="sec-sub">${esc(w.range)}</div>
                <p class="ai-ref">${CAT_REF[c.key]}</p>
                <span class="ai-status st-${c.status}">${esc(c.statusLabel)}</span>
                <p class="ai-evid">${esc(c.evidence)}</p></div></div>`; }).join('')}</div>
        <div class="ai-insight lvl-routine ai-gap">
            <div class="ai-insight-head"><span class="ai-lvl">HEAT-RELATED HEALTH MONITORING</span><span class="ai-status st-${h.status}">${esc(h.statusLabel)}</span></div>
            <div class="ai-sub">Observed pattern</div><p>${esc(h.observed)}</p>
            <div class="ai-sub">What to monitor</div><p>${esc(h.monitor)}</p>
            <div class="ai-sub">Data note</div><p>${esc(h.note)}</p></div>
        <div class="card ai-gap"><div class="card-body"><div class="card-title">Other recurring seasonal patterns</div>
            <ul class="ai-ul">${sea.other.length ? sea.other.map(o => `<li>${esc(o.name)} <span>higher in ${esc(o.months)}</span></li>`).join('') : '<li class="ai-none">No other diseases show a recurring seasonal pattern in the available records</li>'}</ul></div></div>`;
    }
    function mountSeasonal() {
        if (!S.result.diseases.length) return;
        const sea = S.result.seasonal, c = sea.cards.find(x => x.key === S.cat);
        mk('season', 'aiSeasonChart', seasonalConfig(sea.moy[S.cat], c ? c.window.months : []));
    }

    /* ---------- TAB 3: Area ---------- */
    const spark = s => { const mx = Math.max(...s, 1), w = 90, h = 22; return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline fill="none" stroke="${NAVY}" stroke-width="1.5" points="${s.map((v, i) => `${(i / Math.max(s.length - 1, 1) * (w - 2) + 1).toFixed(1)},${(h - 2 - v / mx * (h - 4)).toFixed(1)}`).join(' ')}"/></svg>`; };
    const TREND_TXT = { increasing: 'Increasing', decreasing: 'Decreasing', stable: 'Stable', insufficient: 'Not enough data' };

    function tabArea() {
        const r = S.result; if (!r.diseases.length) return noData();
        const A = E.analyzeArea(r, S.area === '__all' ? null : S.area), top = A.areas[0], what = A.disease || 'all diseases';
        return `<div class="card"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">Disease Cases by Area</div><div class="sec-sub">Recorded cases • ${esc(what)} • ${esc(rangeText())}</div></div>
                <select class="ai-select" id="aiAreaSel" aria-label="Select disease">${diseaseOptions(S.area, `<option value="__all" ${S.area === '__all' ? 'selected' : ''}>All diseases</option>`)}</select></div>
            ${A.areas.length ? `<div style="height:${Math.max(180, Math.min(A.areas.length, 10) * 34 + 40)}px;position:relative"><canvas id="aiAreaChart"></canvas></div>
            <div class="ai-insight lvl-routine"><div class="ai-insight-head"><span class="ai-lvl">HIGHEST RECORDED CASES: ${esc(top.name.toUpperCase())}</span><span class="ai-src">Generated from recorded data</span></div>
                <p>${esc(top.name)} has the most recorded ${A.disease ? esc(A.disease) + ' ' : ''}cases in this period: ${top.cases} of ${A.total} (${top.share}%). ${A.areas.length > 1 ? `The next highest is ${esc(A.areas[1].name)} with ${A.areas[1].cases} (${A.areas[1].share}%).` : 'Only one area has records.'} ${top.trend === 'increasing' ? 'Cases there rose over the last 3 months compared with the 3 months before.' : top.trend === 'decreasing' ? 'Cases there fell over the last 3 months compared with the 3 months before.' : ''}</p>
                <p class="ai-evid">Areas (purok, zone or street) are read from the address recorded for each patient. This reflects recorded counts only. It does not by itself mean higher risk, because population size and recording practices differ between areas.</p></div>` : emptyMsg('No cases recorded for this selection.')}</div></div>
        ${A.areas.length ? `<div class="card ai-gap"><div class="card-body"><div class="ai-head"><div class="card-title">Distribution by area</div><span class="ai-tag rec">Recorded Data</span></div>
            <div class="table-wrap"><table><thead><tr><th>Area</th><th>Cases</th><th>Share</th><th>Monthly trend</th><th>Last 3 months vs prior 3</th></tr></thead><tbody>
            ${A.areas.map(a => `<tr><td>${esc(a.name)}</td><td>${a.cases}</td><td>${a.share}%</td><td>${spark(a.series)}</td>
                <td>${TREND_TXT[a.trend]}${a.delta3 != null && a.trend !== 'insufficient' ? ` (${a.delta3 > 0 ? '+' : ''}${a.delta3} cases)` : ''}</td></tr>`).join('')}</tbody></table></div>
            ${A.unspecified ? `<div class="ai-note">${A.unspecified} case${A.unspecified === 1 ? ' has' : 's have'} no area that could be read from the patient\'s address (or no linked patient).</div>` : ''}</div></div>
        <div class="card ai-gap"><div class="card-body"><div class="card-title">Areas with notable changes</div><ul class="ai-ul">
            ${A.notable.length ? A.notable.map(a => `<li>${esc(a.name)} <span>${a.trend === 'increasing' ? 'Increased' : 'Decreased'} by ${Math.abs(a.delta3)} cases (last 3 months vs prior 3)</span></li>`).join('') : '<li class="ai-none">No notable changes in this selection</li>'}</ul></div></div>` : ''}`;
    }
    function mountArea() {
        const A = E.analyzeArea(S.result, S.area === '__all' ? null : S.area);
        if (A.areas.length) mk('area', 'aiAreaChart', areaConfig(A));
    }

    /* ---------- TAB 4: Forecast ---------- */
    function tabForecast() {
        const r = S.result, s = r.sufficiency; if (!r.diseases.length) return noData();
        if (s.level === 'insufficient') return `<div class="card"><div class="card-body">${emptyMsg(esc(s.message))}</div></div>`;
        const OUT = { may_rise: 'Cases may rise above recent levels', may_fall: 'Cases may fall below recent levels', near_recent: 'Cases may stay near recent levels' };
        return `<div class="card"><div class="card-body">
            <div class="ai-head"><div><div class="card-title">Possible future disease trends</div><div class="sec-sub">Next 3 months • based on ${s.months} months of recorded cases</div></div><span class="ai-tag fc">AI Forecast</span></div>
            <p class="ai-evid">Forecasts show possible ranges, not certainties. Wider ranges mean less reliable estimates.</p>
            <div class="table-wrap"><table><thead><tr><th>Disease</th><th>Confidence</th><th>Possible cases per month (range)</th><th>Outlook</th><th>Months for closer monitoring</th></tr></thead><tbody>
            ${r.diseases.slice(0, 15).map(d => { const f = d.forecast; return f.status !== 'ok'
                ? `<tr><td>${esc(d.name)}</td><td colspan="4" class="ai-none">Insufficient historical data for a reliable forecast (needs ${E.CONFIG.minMonthsForecast}+ months and ${E.CONFIG.minCasesForecast}+ cases; has ${s.months} months, ${d.total} cases)</td></tr>`
                : `<tr><td>${esc(d.name)}</td><td><span class="ai-conf cf-${f.confidence}">${CONF_TEXT[f.confidence]}</span></td>
                    <td>${f.points.map(p => `${esc(p.label)}: ${p.low}–${p.high}`).join('<br>')}</td><td>${OUT[f.outlook]}</td><td>${f.watch.length ? esc(f.watch.join(', ')) : '—'}</td></tr>`; }).join('')}</tbody></table></div>
            <div class="chart-footnote"><i class="fa-regular fa-circle-question"></i><span>Method: damped-trend projection${s.months >= 24 ? ', adjusted for seasonality where records support it' : ''}. Confidence reflects history length and how closely the method matched past months.</span></div></div></div>`;
    }

    /* ---------- events ---------- */
    document.addEventListener('click', e => {
        const t = e.target.closest('[data-aitab]');
        if (t) { S.tab = t.dataset.aitab; document.querySelectorAll('[data-aitab]').forEach(b => b.classList.toggle('active', b === t)); renderTab(); return; }
        const row = e.target.closest('[data-aidis]');
        if (row && S.result) { S.disease = S.result.diseases[+row.dataset.aidis].name; renderTab(); $('aiPanels').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
    document.addEventListener('change', e => {
        if (e.target.id === 'aiDisSel') { S.disease = e.target.value; renderTab(); }
        if (e.target.id === 'aiCatSel') { S.cat = e.target.value; renderTab(); }
        if (e.target.id === 'aiAreaSel') { S.area = e.target.value; renderTab(); }
    });

    /* ---------- export / print ---------- */
    window.printAIInsights = function () {
        if (S.tab === 'resources' && window.ResourceUI) { window.ResourceUI.print(); return; }   // Resource Allocation report
        const r = S.result; if (!r) { showToast('Load AI Insights first.', 'error'); return; }
        const top = r.monitored.slice(0, 3), sea = r.seasonal, h = r.heat, A = E.analyzeArea(r, null);
        const trendImgs = (top.length ? top : [r.diseases[0]]).filter(Boolean).map(d => ({ d, img: imgOf(trendConfig(d)) }));
        const overview = imgOf(trendConfig(null));
        const areaImg = A.areas.length ? imgOf(areaConfig(A), 900, Math.max(180, Math.min(A.areas.length, 10) * 30 + 40)) : '';
        const seaImgs = [['all', 'All recorded diseases', []], ...sea.cards.filter(c => c.ev.cases > 0).map(c => [c.key, c.label, c.window.months])]
            .map(([k, l, m]) => ({ l, img: imgOf(seasonalConfig(sea.moy[k], m), 900, 220) }));
        const user = (typeof getCurrentUser === 'function' ? getCurrentUser() : {}) || {};
        const fcRows = r.diseases.slice(0, 15).map(d => d.forecast.status === 'ok'
            ? `<tr><td>${esc(d.name)}</td><td>${CONF_TEXT[d.forecast.confidence]}</td><td>${d.forecast.points.map(p => `${esc(p.label)}: ${p.low}–${p.high}`).join('; ')}</td><td>${d.forecast.watch.length ? esc(d.forecast.watch.join(', ')) : '—'}</td></tr>`
            : `<tr><td>${esc(d.name)}</td><td colspan="3">Insufficient historical data for a reliable forecast</td></tr>`).join('');
        const html = `
        <h1>AI Insights Report</h1>
        <div class="p-meta"><div><b>Health center:</b> ${esc($('barangayLabel')?.textContent || '')}</div><div><b>Date range:</b> ${esc(rangeText())}</div>
            <div><b>Generated:</b> ${esc(new Date().toLocaleString('en-PH', { dateStyle: 'long', timeStyle: 'short' }))}${user.name ? ' by ' + esc(user.name) : ''}</div></div>
        <p class="p-note">Decision-support only. Findings describe patterns in recorded data. <b>Recorded Data</b> = cases stored in the system. <b>Observed Trend</b> = pattern found in historical records. <b>AI Forecast</b> = possible future pattern based on available data.</p>

        <h2>1. Disease trend analysis</h2>
        <img src="${overview}" alt="Top diseases by month">
        <table><thead><tr><th>Disease</th><th>Cases</th><th>Pattern (observed trend)</th><th>Basis</th></tr></thead><tbody>
        ${r.diseases.slice(0, 15).map(d => `<tr><td>${esc(d.name)}</td><td>${d.total} (${d.share}%)</td><td>${esc(d.pattern)}</td><td>${esc(d.basis)}</td></tr>`).join('')}</tbody></table>
        ${trendImgs.map(({ d, img }) => `<h3>${esc(d.name)}</h3><img src="${img}" alt="Trend for ${esc(d.name)}">`).join('')}

        <h2>2. AI explanation and monitoring considerations</h2>
        ${(top.length ? top : []).map(d => `<div class="p-ins"><h3>${esc(d.insight.title)}</h3>
            <p><b>Why it was identified:</b> ${esc(d.insight.why)}</p><p><b>What to monitor:</b> ${esc(d.insight.monitor)}</p><p><b>Recommended monitoring period:</b> ${esc(d.insight.period)}</p></div>`).join('') || '<p>No disease currently meets the criteria for increased monitoring.</p>'}

        <h2>3. Area analysis (all diseases)</h2>
        ${A.areas.length ? `<img src="${areaImg}" alt="Cases by area"><table><thead><tr><th>Area</th><th>Cases</th><th>Share</th><th>Last 3 months vs prior 3</th></tr></thead><tbody>
        ${A.areas.map((a, i) => `<tr><td>${esc(a.name)}${i === 0 ? ' (highest recorded cases)' : ''}</td><td>${a.cases}</td><td>${a.share}%</td><td>${TREND_TXT[a.trend]}${a.delta3 != null && a.trend !== 'insufficient' ? ` (${a.delta3 > 0 ? '+' : ''}${a.delta3})` : ''}</td></tr>`).join('')}</tbody></table>
        <p class="p-note">Counts are recorded cases only and do not by themselves indicate higher risk.</p>` : '<p>No cases recorded.</p>'}

        <h2>4. Seasonal analysis</h2>
        ${seaImgs.map(x => `<h3>${esc(x.l)}</h3><img src="${x.img}" alt="">`).join('')}
        <table><thead><tr><th>Window</th><th>Finding</th></tr></thead><tbody>
        ${sea.cards.map(c => `<tr><td>${esc(c.window.label)} (${esc(c.window.range)}): ${esc(c.label)}</td><td><b>${esc(c.statusLabel)}.</b> ${esc(c.evidence)}</td></tr>`).join('')}</tbody></table>
        <div class="p-ins"><h3>HEAT-RELATED HEALTH MONITORING</h3><p><b>Observed pattern:</b> ${esc(h.observed)}</p><p><b>What to monitor:</b> ${esc(h.monitor)}</p><p><b>Data note:</b> ${esc(h.note)}</p></div>

        <h2>5. Forecasting results</h2>
        ${r.sufficiency.level === 'insufficient' ? `<p>${esc(r.sufficiency.message)}</p>` : `<table><thead><tr><th>Disease</th><th>Confidence</th><th>Possible cases per month (range)</th><th>Months for closer monitoring</th></tr></thead><tbody>${fcRows}</tbody></table>`}

        <h2>6. Data limitations and confidence</h2>
        <ul><li><b>Data sufficiency:</b> ${esc(LEVEL_TEXT[r.sufficiency.level])}. ${esc(r.sufficiency.message)}</li>
        ${r.sufficiency.notes.map(n => `<li>${esc(n)}</li>`).join('')}
        <li>Forecasts are possible ranges, not certainties, and are shown only when enough history exists.</li>
        <li>Final interpretation and health decisions remain with authorized health workers.</li></ul>
        <div class="p-foot">Report generated ${esc(new Date().toLocaleDateString('en-PH', { dateStyle: 'long' }))} • ${esc(rangeText())}</div>`;
        let el = $('aiPrintReport'); if (!el) { el = document.createElement('div'); el.id = 'aiPrintReport'; document.body.appendChild(el); }
        el.innerHTML = html;
        document.body.classList.add('ai-printing');
        const done = () => { document.body.classList.remove('ai-printing'); window.removeEventListener('afterprint', done); };
        window.addEventListener('afterprint', done);
        setTimeout(() => window.print(), 200);
    };
})();