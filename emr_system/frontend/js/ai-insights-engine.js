/* ============================================================
   AI INSIGHTS — analysis engine
   Pure functions, no DOM. Input: aggregated case rows
     { month:'YYYY-MM' | date:'YYYY-MM-DD', disease, barangay, cases, age_group? }
   Output: trends, seasonality, area breakdown, forecasts, and
   plain-language findings built ONLY from the supplied records.
   No patient identifiers are used or required.
   ============================================================ */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.AIEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    /* ---- Tunable settings (reference windows & thresholds, NOT conclusions) ---- */
    const CONFIG = {
        minMonthsTrend: 6,        // months needed to describe a trend
        minMonthsForecast: 12,    // months needed before any forecast is shown
        minCasesTrend: 8,         // total cases needed to describe a disease trend
        minCasesForecast: 20,
        minCasesSeasonal: 15,
        horizon: 3,               // months forecast ahead
        // Reference calendar windows used only as hypotheses to test against records
        seasons: {
            cooler: { label: 'Cooler months',            range: 'December to February', months: [12, 1, 2] },
            warmer: { label: 'Warmer / hottest months',  range: 'March to May',         months: [3, 4, 5] },
            rainy:  { label: 'Rainy months',             range: 'June to November',     months: [6, 7, 8, 9, 10, 11] }
        },
        // Name matching only groups diseases for testing; it never asserts a season.
        categories: {
            respiratory: { label: 'Respiratory illness',  season: 'cooler', match: /influenza|\bflu\b|\bubo\b|cough|\bcold\b|pneumonia|bronch|asthma|respiratory|\buri\b|urti|covid|tubercul|\btb\b/i },
            vector:      { label: 'Vector-borne disease', season: 'rainy',  match: /dengue|malaria|chikungunya|zika|filaria/i },
            heat:        { label: 'Heat-related illness', season: 'warmer', match: /heat|sunstroke|hyperthermia/i }
        },
        communicable: /dengue|influenza|\bflu\b|covid|tubercul|\btb\b|diarrh|measles|pneumonia|cholera|typhoid|hepatitis|leptospir|chickenpox|varicella|malaria|respiratory|\buri\b|urti|scabies|conjunctivitis|hand.?foot/i
    };

    const MN = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const MFULL = ['January','February','March','April','May','June','July','August','September','October','November','December'];

    /* ---- helpers ---- */
    const sum = a => a.reduce((x, y) => x + y, 0);
    const mean = a => a.length ? sum(a) / a.length : 0;
    const sd = a => { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(sum(a.map(x => (x - m) ** 2)) / (a.length - 1)); };
    const r1 = x => Math.round(x * 10) / 10;
    const pct = x => Math.round(x * 10) / 10;
    const ymParts = ym => { const [y, m] = ym.split('-').map(Number); return { y, m }; };
    const ymAdd = (ym, k) => { const { y, m } = ymParts(ym); const t = y * 12 + (m - 1) + k; return `${Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, '0')}`; };
    const ymRange = (a, b) => { const o = []; let c = a; while (c <= b) { o.push(c); c = ymAdd(c, 1); } return o; };
    const ymLabel = ym => { const { y, m } = ymParts(ym); return `${MN[m - 1]} ${y}`; };
    const monthList = ms => { const s = [...ms].sort((a, b) => a - b); return s.map(m => MFULL[m - 1]).join(', '); };
    const ordinal = n => ['', 'most frequently recorded', '2nd most frequent', '3rd most frequent'][n] || `${n}th most frequent`;

    /** Only complete calendar months are analysed; a partial month would look like a false drop. */
    function completeMonths(start, end) {
        const sm = start.slice(0, 7), em = end.slice(0, 7);
        const first = Number(start.slice(8, 10)) === 1 ? sm : ymAdd(sm, 1);
        const { y, m } = ymParts(em);
        const last = Number(end.slice(8, 10)) >= new Date(y, m, 0).getDate() ? em : ymAdd(em, -1);
        return first <= last ? ymRange(first, last) : [];
    }

    /* ---- trend metrics ---- */
    function trendMetrics(s, cfg) {
        const n = s.length, total = sum(s);
        const last3 = s.slice(-3), prev3 = s.slice(-6, -3);
        const recent = mean(last3), prior = prev3.length === 3 ? mean(prev3) : null;
        let incStreak = 0; for (let i = n - 1; i > 0 && s[i] > s[i - 1]; i--) incStreak++;
        let activeStreak = 0; for (let i = n - 1; i >= 0 && s[i] > 0; i--) activeStreak++;
        // least-squares slope over the last 6 months
        const w = s.slice(-6), xm = (w.length - 1) / 2, ym = mean(w);
        const den = sum(w.map((_, i) => (i - xm) ** 2));
        const slope = den ? sum(w.map((v, i) => (i - xm) * (v - ym))) / den : 0;
        const changePct = prior > 0 ? ((recent - prior) / prior) * 100 : null;

        let pattern = 'insufficient';
        if (n >= cfg.minMonthsTrend && total >= cfg.minCasesTrend && prior !== null) {
            const diff3 = (recent - prior) * 3;   // change in 3-month totals
            if (diff3 >= 2 && (prior === 0 || recent >= 1.25 * prior) && slope > 0) pattern = 'increasing';
            else if (-diff3 >= 2 && recent <= 0.75 * prior && slope < 0) pattern = 'decreasing';
            else pattern = 'stable';
        }
        return { recent: r1(recent), prior: prior === null ? null : r1(prior), changePct: changePct === null ? null : Math.round(changePct), incStreak, activeStreak, slope: r1(slope), pattern };
    }

    /* ---- seasonality for a single disease (month-of-year) ---- */
    function diseaseSeasonal(s, months, cfg) {
        const mu = mean(s), total = sum(s);
        const vals = Array.from({ length: 12 }, () => []);
        s.forEach((v, i) => vals[ymParts(months[i]).m - 1].push(v));
        const avg = vals.map(v => v.length ? mean(v) : 0);
        const peaks = [];
        vals.forEach((v, i) => {
            if (v.length && avg[i] >= 1.3 * mu && avg[i] >= 1)
                peaks.push({ m: i + 1, obs: v.length, above: v.filter(x => x > mu).length });
        });
        if (s.length < 12 || total < cfg.minCasesSeasonal) return { status: 'insufficient', avg, peakMonths: [], years: 0 };
        const confirmed = peaks.filter(p => p.obs >= 2 && p.above >= 2);
        if (confirmed.length) return { status: 'supported', avg, peakMonths: confirmed.map(p => p.m), years: Math.min(...confirmed.map(p => p.obs)) };
        if (peaks.length) return { status: 'limited', avg, peakMonths: peaks.map(p => p.m), years: 1 };
        return { status: 'none', avg, peakMonths: [], years: 0 };
    }

    /* ---- test a reference window (e.g. Dec–Feb) against recorded data ---- */
    function windowEvidence(s, months, win) {
        const inIdx = [], outIdx = [];
        months.forEach((ym, i) => (win.months.includes(ymParts(ym).m) ? inIdx : outIdx).push(i));
        const avgIn = mean(inIdx.map(i => s[i])), avgOut = mean(outIdx.map(i => s[i]));
        const keys = {};
        inIdx.forEach(i => {
            const { y, m } = ymParts(months[i]);
            const k = win.months.includes(12) && win.months.includes(1) && m === 12 ? y + 1 : y;
            (keys[k] = keys[k] || []).push(s[i]);
        });
        const seasons = Object.values(keys).filter(v => v.length >= 2);
        const elevated = seasons.filter(v => mean(v) >= Math.max(1.25 * avgOut, avgOut + 0.5)).length;
        const ratio = avgOut > 0 ? avgIn / avgOut : (avgIn > 0 ? Infinity : 0);
        return { avgIn: r1(avgIn), avgOut: r1(avgOut), ratio, inMonths: inIdx.length, outMonths: outIdx.length, seasons: seasons.length, elevated, cases: sum(s) };
    }

    function seasonStatus(ev) {
        if (ev.cases === 0) return 'nodata';
        if (ev.seasons < 1 || ev.outMonths < 2) return 'insufficient';
        if (ev.elevated >= 2 && ev.ratio >= 1.25 && ev.cases >= 10) return 'supported';
        if (ev.elevated >= 1 && ev.ratio >= 1.25) return 'limited';
        return 'not_supported';
    }
    const STATUS_LABEL = {
        supported: 'Supported by records', limited: 'Observed once, not yet confirmed',
        not_supported: 'Not supported by records', insufficient: 'Insufficient data', nodata: 'No matching cases recorded'
    };

    function ratioText(ev) {
        return ev.ratio === Infinity ? 'cases were recorded only within this window' : `${r1(ev.ratio)}× the average in other months`;
    }

    /* ---- Holt damped-trend forecast (optionally seasonally adjusted) ---- */
    function forecast(s, months, seasonal, cfg) {
        const n = s.length, total = sum(s);
        if (n < cfg.minMonthsForecast || total < cfg.minCasesForecast)
            return { status: 'insufficient', confidence: 'insufficient', points: [] };
        const mu = mean(s);
        const useSeason = n >= 24 && seasonal.status === 'supported';
        const idxOf = ym => { const v = seasonal.avg[ymParts(ym).m - 1] / (mu || 1); return Math.min(2, Math.max(0.5, v || 1)); };
        const y = useSeason ? s.map((v, i) => v / idxOf(months[i])) : s.slice();
        const a = 0.5, b = 0.2, phi = 0.9;
        let l = y[0], t = (y[Math.min(3, n - 1)] - y[0]) / Math.min(3, n - 1);
        const errs = [];
        for (let i = 1; i < n; i++) {
            const pred = l + phi * t;
            errs.push((y[i] - pred) * (useSeason ? idxOf(months[i]) : 1));
            const ln = a * y[i] + (1 - a) * (l + phi * t);
            t = b * (ln - l) + (1 - b) * phi * t; l = ln;
        }
        const mae = mean(errs.map(Math.abs)), sigma = sd(errs);
        const relMAE = mae / Math.max(mu, 0.5);
        const points = [];
        for (let h = 1; h <= cfg.horizon; h++) {
            const ym = ymAdd(months[n - 1], h);
            let f = l; for (let i = 1; i <= h; i++) f += Math.pow(phi, i) * t;
            if (useSeason) f *= idxOf(ym);
            const band = 1.28 * sigma * Math.sqrt(h);
            points.push({ ym, label: ymLabel(ym), value: Math.max(0, r1(f)), low: Math.max(0, Math.floor(f - band)), high: Math.max(0, Math.ceil(f + band)) });
        }
        const confidence = n >= 24 && relMAE < 0.35 ? 'high' : relMAE < 0.65 ? 'moderate' : 'low';
        const recent = mean(s.slice(-3));
        const avgF = mean(points.map(p => p.value));
        const outlook = avgF >= 1.25 * recent && avgF - recent >= 0.67 ? 'may_rise' : avgF <= 0.75 * recent && recent - avgF >= 0.67 ? 'may_fall' : 'near_recent';
        const watch = points.filter(p => p.value >= 1.25 * recent && p.value - recent >= 0.67
            || (seasonal.status === 'supported' && seasonal.peakMonths.includes(ymParts(p.ym).m))).map(p => p.label);
        return { status: 'ok', confidence, relMAE: r1(relMAE), usedSeasonality: useSeason, points, outlook, watch, months: n };
    }

    /* ---- per-barangay breakdown (rows already filtered to complete months) ---- */
    function analyzeArea(result, disease) {
        const { months, rows } = result._ctx;
        const sel = disease ? rows.filter(r => r.disease === disease) : rows;
        const map = new Map();
        sel.forEach(r => {
            if (!map.has(r.barangay)) map.set(r.barangay, Array(months.length).fill(0));
            map.get(r.barangay)[months.indexOf(r.month)] += r.cases;
        });
        const total = sum([...map.values()].map(sum));
        const areas = [...map.entries()].map(([name, series]) => {
            const t = trendMetrics(series, { ...CONFIG, minCasesTrend: 5 });
            return { name, series, cases: sum(series), share: total ? pct(sum(series) / total * 100) : 0,
                trend: t.pattern, recent: t.recent, prior: t.prior, changePct: t.changePct,
                delta3: t.prior === null ? null : Math.round((t.recent - t.prior) * 3) };
        }).sort((a, b) => b.cases - a.cases);
        const notable = areas.filter(a => a.trend !== 'stable' && a.trend !== 'insufficient' && Math.abs(a.delta3) >= 3);
        const unspecified = areas.find(a => a.name === 'Unspecified');
        return { disease: disease || null, total, areas, notable, unspecified: unspecified ? unspecified.cases : 0 };
    }

    /* ---- main ---- */
    function analyze(rawRows, opts) {
        const cfg = Object.assign({}, CONFIG, opts.config || {});
        const months = completeMonths(opts.start, opts.end);
        const set = new Set(months);
        const rows = (rawRows || []).map(r => ({
            month: String(r.month || r.date || '').slice(0, 7),
            disease: String(r.disease || '').trim(),
            barangay: String(r.barangay || '').trim() || 'Unspecified',
            cases: Number(r.cases == null ? 1 : r.cases),
            age_group: r.age_group || null,
            communicable: r.is_communicable
        })).filter(r => r.disease && set.has(r.month) && r.cases > 0);

        const n = months.length;
        const pos = new Map(months.map((m, i) => [m, i]));
        const byD = new Map();
        rows.forEach(r => {
            if (!byD.has(r.disease)) byD.set(r.disease, { series: Array(n).fill(0), comm: false });
            const o = byD.get(r.disease); o.series[pos.get(r.month)] += r.cases; if (r.communicable === true) o.comm = true;
        });
        const allSeries = Array(n).fill(0);
        byD.forEach(o => o.series.forEach((v, i) => allSeries[i] += v));
        const grand = sum(allSeries), active = allSeries.filter(v => v > 0).length;

        /* data sufficiency */
        let level = n < 6 ? 'insufficient' : n < 12 ? 'limited' : n < 24 ? 'moderate' : 'good';
        const msg = {
            insufficient: 'Insufficient historical data to generate a reliable disease forecast. Continue recording cases to improve future analysis.',
            limited: `${n} complete months of records are available. Trends can be described, but forecasts and seasonal patterns are not yet reliable.`,
            moderate: `${n} complete months of records are available. Forecasts are shown with wider ranges; confirming recurring seasons needs at least two years of records.`,
            good: `${n} complete months of records are available, enough to compare seasons across years.`
        }[level];
        const notes = [];
        if (n && active < 0.7 * n) notes.push(`Cases were recorded in only ${active} of ${n} months; gaps may reflect missing entries rather than no cases.`);
        const startPartial = Number(opts.start.slice(8, 10)) !== 1, endPartial = months.length && months[months.length - 1] !== opts.end.slice(0, 7);
        if (startPartial || endPartial) notes.push('Partial months at the edges of the selected range are excluded so incomplete counts are not mistaken for a decline.');
        notes.push('Temperature and other environmental data are not integrated; seasonal findings describe recorded cases only.');

        /* per disease */
        const ranked = [...byD.entries()].map(([name, o]) => ({ name, ...o, total: sum(o.series) })).sort((a, b) => b.total - a.total);
        const diseases = ranked.map((o, i) => {
            const catKey = Object.keys(cfg.categories).find(k => cfg.categories[k].match.test(o.name)) || null;
            const metrics = trendMetrics(o.series, cfg);
            const seasonal = diseaseSeasonal(o.series, months, cfg);
            const fc = forecast(o.series, months, seasonal, cfg);
            return {
                name: o.name, series: o.series, total: o.total, rank: i + 1, share: pct(o.total / grand * 100),
                category: catKey, communicable: o.comm || cfg.communicable.test(o.name),
                metrics, seasonal, forecast: fc
            };
        });

        const result = { range: { start: opts.start, end: opts.end }, months, monthLabels: months.map(ymLabel), grandTotal: grand, allSeries,
            sufficiency: { level, message: msg, months: n, activeMonths: active, notes }, diseases, _ctx: { months, rows, cfg } };

        /* seasonal categories */
        result.seasonal = { cards: [], moy: {}, other: [] };
        const moyOf = s => { const v = Array.from({ length: 12 }, () => []); s.forEach((x, i) => v[ymParts(months[i]).m - 1].push(x)); return v.map(a => a.length ? r1(mean(a)) : null); };
        result.seasonal.moy.all = moyOf(allSeries);
        Object.entries(cfg.categories).forEach(([key, cat]) => {
            const matched = diseases.filter(d => d.category === key);
            const s = Array(n).fill(0); matched.forEach(d => d.series.forEach((v, i) => s[i] += v));
            const win = cfg.seasons[cat.season];
            const ev = n >= 6 ? windowEvidence(s, months, win) : { cases: sum(s), seasons: 0, outMonths: 0, ratio: 0, avgIn: 0, avgOut: 0, elevated: 0 };
            const status = n < 12 && ev.cases > 0 ? 'insufficient' : seasonStatus(ev);
            result.seasonal.moy[key] = moyOf(s);
            result.seasonal.cards.push({ key, label: cat.label, season: cat.season, window: win, diseases: matched.map(d => d.name), status, statusLabel: STATUS_LABEL[status], ev,
                evidence: seasonEvidenceText(cat, win, matched, status, ev, n) });
        });
        result.seasonal.other = diseases.filter(d => !d.category && d.seasonal.status === 'supported')
            .map(d => ({ name: d.name, months: monthList(d.seasonal.peakMonths), years: d.seasonal.years }));

        /* summary lists */
        result.summary = {
            increasing: diseases.filter(d => d.metrics.pattern === 'increasing'),
            decreasing: diseases.filter(d => d.metrics.pattern === 'decreasing'),
            frequent: diseases.slice(0, 5)
        };

        /* monitoring level + insight text */
        const nextMonth = n ? ymAdd(months[n - 1], 1) : null;
        diseases.forEach(d => {
            d.pattern = patternLabel(d);
            d.basis = basisText(d, months);
            d.monitoring = monitoring(d, level, nextMonth, months);
            d.insight = buildInsight(d, result, months);
        });
        result.monitored = diseases.filter(d => d.monitoring.level !== 'insufficient' && d.monitoring.level !== 'routine')
            .sort((a, b) => b.monitoring.score - a.monitoring.score);
        result.heat = heatInsight(result);
        return result;
    }

    function seasonEvidenceText(cat, win, matched, status, ev, n) {
        const names = matched.map(d => d.name).join(', ');
        if (status === 'nodata') return `No ${cat.label.toLowerCase()} cases were found in the selected records, so no pattern can be described.`;
        if (status === 'insufficient') return `Only ${n} month${n === 1 ? '' : 's'} of records are available. At least 12 months are needed to compare ${win.range} with other months (${names}).`;
        const base = `${win.range}: ${ev.avgIn} cases per month versus ${ev.avgOut} in other months (${ratioText(ev)}). Diseases included: ${names}.`;
        if (status === 'supported') return `${base} The window was higher than other months in ${ev.elevated} of ${ev.seasons} observed seasons.`;
        if (status === 'limited') return `${base} Higher activity was seen in ${ev.elevated} of ${ev.seasons} observed season${ev.seasons === 1 ? '' : 's'}, which is not enough to call it recurring.`;
        return `${base} The records do not show this window standing out from other months.`;
    }

    function patternLabel(d) {
        const m = d.metrics.pattern;
        if (m === 'insufficient') return 'Insufficient data';
        const t = m === 'increasing' ? 'Increasing pattern' : m === 'decreasing' ? 'Decreasing pattern' : 'Stable pattern';
        return d.seasonal.status === 'supported' ? (m === 'stable' ? 'Recurring seasonal pattern' : `${t}, recurring seasonal`) : t;
    }

    function basisText(d, months) {
        const m = d.metrics, parts = [];
        if (m.pattern === 'insufficient') return `${d.total} case${d.total === 1 ? '' : 's'} recorded over ${months.length} months, which is too few to describe a pattern.`;
        if (m.pattern === 'stable') {
            const w = d.series.slice(-6); parts.push(`Monthly cases stayed between ${Math.min(...w)} and ${Math.max(...w)} over the last ${w.length} months (average ${r1(mean(w))}).`);
        } else {
            parts.push(`The last 3 months averaged ${m.recent} cases per month, compared with ${m.prior} in the 3 months before${m.changePct !== null ? ` (${m.changePct > 0 ? '+' : ''}${m.changePct}%)` : ''}.`);
            if (m.pattern === 'increasing' && m.incStreak >= 2) parts.push(`Cases rose in ${m.incStreak} consecutive months.`);
        }
        if (d.seasonal.status === 'supported') parts.push(`Higher activity recurred in ${monthList(d.seasonal.peakMonths)}.`);
        return parts.join(' ');
    }

    function monitoring(d, level, nextMonth, months) {
        const m = d.metrics, reasons = []; let score = 0;
        if (m.pattern === 'insufficient' || level === 'insufficient') return { level: 'insufficient', score: 0, reasons: ['Too few records to assess.'] };
        if (m.pattern === 'increasing') { score += 30; reasons.push('increasing pattern'); if (m.incStreak >= 2) { score += 10; reasons.push(`${m.incStreak} consecutive monthly increases`); } }
        if (m.activeStreak >= 6) { score += 15; reasons.push(`cases recorded in each of the last ${m.activeStreak} months`); }
        else if (m.activeStreak >= 3 && m.recent >= 1) { score += 10; reasons.push(`cases recorded in each of the last ${m.activeStreak} months`); }
        if (d.rank === 1) score += 20; else if (d.rank <= 3) score += 12; else if (d.rank <= 5) score += 6;
        if (d.communicable) { score += 15; reasons.push('communicable disease'); }
        if (d.seasonal.status === 'supported' && nextMonth) {
            const nm = ymParts(nextMonth).m, cm = ymParts(months[months.length - 1]).m;
            if (d.seasonal.peakMonths.includes(nm)) { score += 15; reasons.push('next month falls in a historically higher period'); }
            else if (d.seasonal.peakMonths.includes(cm)) score += 10;
        }
        if (d.forecast.status === 'ok' && d.forecast.confidence !== 'low' && d.forecast.outlook === 'may_rise') { score += 10; reasons.push('forecast suggests cases may rise'); }
        score = Math.min(100, score);
        let lvl = score >= 60 ? 'high' : score >= 35 ? 'moderate' : 'routine';
        if (level === 'limited' && lvl === 'high') lvl = 'moderate';     // short history: never top level
        return { level: lvl, score, reasons };
    }

    function buildInsight(d, result, months) {
        const m = d.metrics, lvl = d.monitoring.level;
        const title = lvl === 'insufficient' ? `INSUFFICIENT DATA: ${d.name.toUpperCase()}` : `${lvl.toUpperCase()} MONITORING: ${d.name.toUpperCase()}`;
        const why = [];
        if (lvl === 'insufficient') why.push(d.basis);
        else {
            why.push(d.basis);
            if (m.activeStreak >= 3 && m.pattern !== 'stable') why.push(`Cases were recorded in each of the last ${m.activeStreak} months.`);
            why.push(`It makes up ${d.share}% of all recorded cases (${ordinal(d.rank)}).`);
            if (d.communicable) why.push('It is a communicable disease, so repeated cases may be worth following closely.');
            const age = ageNote(result, d, months); if (age) why.push(age);
            if (m.pattern === 'increasing') why.push(`If this pattern continues, ${d.name} may remain among the more frequently recorded illnesses in the coming months.`);
        }
        const area = analyzeArea(result, d.name), top = area.areas[0];
        const mon = [`Monitor new ${d.name} cases and whether monthly counts ${m.pattern === 'increasing' ? 'keep rising' : m.pattern === 'decreasing' ? 'keep falling' : 'move away from the recent level'}.`];
        const yoy = yearAgo(d, months); if (yoy) mon.push(yoy);
        if (top && area.areas.length > 1) mon.push(`Highest recorded cases so far: ${top.name} (${top.cases} cases, ${top.share}% of this disease's records).`);
        let period;
        if (d.seasonal.status === 'supported') period = `Historical records show higher ${d.name} activity in ${monthList(d.seasonal.peakMonths)}, seen in at least ${d.seasonal.years} years of data.`;
        else if (d.seasonal.status === 'limited') period = `Higher activity appeared in ${monthList(d.seasonal.peakMonths)} in one year of data. More history is needed to confirm a recurring period.`;
        else period = 'No recurring high-activity period can be identified from the available records.';
        if (d.forecast.status === 'ok' && d.forecast.watch.length) period += ` The forecast suggests closer monitoring in ${d.forecast.watch.join(', ')}.`;
        return { title, level: lvl, why: why.join(' '), monitor: mon.join(' '), period, source: 'rule' };
    }

    function ageNote(result, d, months) {
        const recent = new Set(months.slice(-3));
        const rs = result._ctx.rows.filter(r => r.disease === d.name && recent.has(r.month) && r.age_group);
        const tot = sum(rs.map(r => r.cases)); if (tot < 5) return '';
        const kid = sum(rs.filter(r => r.age_group === 'child').map(r => r.cases)), old = sum(rs.filter(r => r.age_group === 'senior').map(r => r.cases));
        if (kid / tot >= 0.4) return `${kid} of ${tot} recent cases with a recorded age group were among children.`;
        if (old / tot >= 0.4) return `${old} of ${tot} recent cases with a recorded age group were among older adults.`;
        return '';
    }

    function yearAgo(d, months) {
        const n = months.length; if (n < 15) return '';
        const now = sum(d.series.slice(-3)), then = sum(d.series.slice(-15, -12));
        return `Last year the same 3-month period recorded ${then} case${then === 1 ? '' : 's'}, compared with ${now} now.`;
    }

    function heatInsight(result) {
        const c = result.seasonal.cards.find(x => x.key === 'heat'), s = result.sufficiency;
        let observed;
        if (c.status === 'nodata') observed = 'No heat-related cases (such as heat exhaustion or heat stroke) were found in the selected records, so no pattern can be described.';
        else if (c.status === 'supported') observed = `Heat-related cases were recorded more often during ${c.window.range} in the available records. ${c.evidence}`;
        else if (c.status === 'limited') observed = `Heat-related cases were higher in ${c.window.range} in one observed season. ${c.evidence}`;
        else if (c.status === 'insufficient') observed = c.evidence;
        else observed = `Heat-related cases do not show a clear concentration in ${c.window.range}. ${c.evidence}`;
        return {
            status: c.status, statusLabel: c.statusLabel, observed,
            monitor: 'Health workers may pay closer attention to heat-related symptoms, particularly among vulnerable groups such as older adults, during periods of high heat exposure.',
            note: 'Temperature data is not integrated in this system. This describes recorded cases only and does not predict that any specific month will be hotter. External weather information would be a separate data source.',
            reliable: s.level === 'good' || s.level === 'moderate'
        };
    }

    return { analyze, analyzeArea, completeMonths, CONFIG, ymLabel, STATUS_LABEL, MFULL, MN };
});
