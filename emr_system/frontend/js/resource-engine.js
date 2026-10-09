/* ============================================================
   RESOURCE ALLOCATION — analysis engine
   Pure functions, no DOM. Input: aggregated rows from
   GET /api/ai-insights/resources  (no patient identifiers).
   Output: usage, trends, stock status, seasonal resource patterns,
   disease links, alerts, and plain-language findings built ONLY
   from the supplied records.

   Rules this engine follows:
   - A resource is tied to a health pattern ONLY when the records
     support it (actual dispensed prescriptions + usage figures).
   - It never assumes a medicine is "needed" because a disease is common.
   - It never claims a month will be hotter/wetter (no weather data).
   - It never buys, orders, distributes, or changes inventory.
   ============================================================ */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory(require('./ai-insights-engine.js'));
    else root.ResourceEngine = factory(root.AIEngine);
})(typeof self !== 'undefined' ? self : this, function (E) {
    'use strict';

    /* ---- Tunable thresholds (not conclusions) ---- */
    const CFG = {
        minMonthsTrend: 6,        // complete months needed to describe a usage trend
        minUnitsTrend: 10,        // units needed before a trend is described
        trendPct: 20,             // recent 3 months vs prior 3 months (%)
        minMonthsSeasonal: 12,    // complete months needed for any seasonal comparison
        minUnitsSeasonal: 10,     // units inside the window before it counts as "higher usage"
        seasonRatio: 1.25,        // window average must be >= 1.25x the average of other months
        minInWindow: 2, minOutWindow: 3,
        minRxLink: 3,             // dispensed prescriptions needed to say an item is linked to a disease group
        minCasesSeasonal: (E.CONFIG && E.CONFIG.minCasesSeasonal) || 15,
        minCasesTrend: 8,
        consistentShare: 0.75,    // share of months with usage to count as "consistent"
        lowCoverageMonths: 1,     // < 1 month of supply at recent usage = low
        approachingDays: 62,      // a window starting within ~2 months is "approaching"
        corrMin: 0.6, corrMinMonths: 6,
        maxListed: 6
    };

    const MN = E.MN || ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const MFULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

    /* ---- helpers ---- */
    const sum = a => a.reduce((x, y) => x + y, 0);
    const mean = a => a.length ? sum(a) / a.length : 0;
    const r1 = x => Math.round(x * 10) / 10;
    const ymParts = ym => { const [y, m] = ym.split('-').map(Number); return { y, m }; };
    const ymAdd = (ym, k) => { const { y, m } = ymParts(ym); const t = y * 12 + (m - 1) + k; return `${Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, '0')}`; };
    const ymRange = (a, b) => { const o = []; let c = a; while (c <= b) { o.push(c); c = ymAdd(c, 1); } return o; };
    const ymLabel = ym => { const { y, m } = ymParts(ym); return `${MN[m - 1]} ${y}`; };
    const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
    const list = a => a.length <= 1 ? (a[0] || '') : a.length === 2 ? `${a[0]} and ${a[1]}` : `${a.slice(0, -1).join(', ')}, and ${a[a.length - 1]}`;

    /** Only complete calendar months are analysed; a partial month would look like a false drop. */
    function completeMonths(start, end) {
        const sm = start.slice(0, 7), em = end.slice(0, 7);
        const first = Number(start.slice(8, 10)) === 1 ? sm : ymAdd(sm, 1);
        const { y, m } = ymParts(em);
        const last = Number(end.slice(8, 10)) >= new Date(y, m, 0).getDate() ? em : ymAdd(em, -1);
        return first <= last ? ymRange(first, last) : [];
    }

    function periodText(months, start, end) {
        const a = months.length ? months[0] : start.slice(0, 7), b = months.length ? months[months.length - 1] : end.slice(0, 7);
        const pa = ymParts(a), pb = ymParts(b);
        return pa.y === pb.y ? (pa.m === pb.m ? `${MFULL[pa.m - 1]} ${pa.y}` : `${MFULL[pa.m - 1]}–${MFULL[pb.m - 1]} ${pa.y}`)
            : `${MN[pa.m - 1]} ${pa.y} – ${MN[pb.m - 1]} ${pb.y}`;
    }

    function pearson(a, b) {
        const n = a.length; if (n < CFG.corrMinMonths || n !== b.length) return null;
        const ma = mean(a), mb = mean(b); let sab = 0, saa = 0, sbb = 0;
        for (let i = 0; i < n; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) ** 2; sbb += (b[i] - mb) ** 2; }
        return saa === 0 || sbb === 0 ? null : sab / Math.sqrt(saa * sbb);
    }

    /* ---- usage trend (recent 3 complete months vs the 3 before) ---- */
    function trendOf(series, total) {
        if (series.length < CFG.minMonthsTrend || total < CFG.minUnitsTrend) return { status: 'insufficient', recent: null, prior: null, pct: null };
        const recent = mean(series.slice(-3)), prior = mean(series.slice(-6, -3)), diff = recent - prior;
        const pct = prior > 0 ? diff / prior * 100 : null;
        let status = 'stable';
        if (prior === 0 && recent > 0) status = 'increasing';
        else if (pct !== null && pct >= CFG.trendPct && diff >= 1) status = 'increasing';
        else if (pct !== null && pct <= -CFG.trendPct && -diff >= 1) status = 'decreasing';
        return { status, recent: r1(recent), prior: r1(prior), pct: pct === null ? null : Math.round(pct) };
    }

    /* ---- current stock status (decision-support wording, not a purchase order) ---- */
    function stockOf(item, series) {
        if (!item) return { status: 'unknown', label: 'Not linked to inventory', why: 'No matching inventory item.' };
        const stock = item.stock || 0, reorder = item.reorder || 0;
        const recentAvg = series.length >= 3 ? mean(series.slice(-3)) : mean(series);
        const coverage = recentAvg > 0 ? stock / recentAvg : null;
        let status = 'adequate', why = `Stock is above the reorder level (${reorder}).`;
        if (stock <= 0) { status = 'out'; why = 'No stock remaining.'; }
        else if (stock <= reorder) { status = 'low'; why = `Stock (${stock}) is at or below the reorder level (${reorder}).`; }
        else if (coverage !== null && coverage < CFG.lowCoverageMonths) { status = 'low'; why = `Stock (${stock}) is less than one month of the recent average usage (${r1(recentAvg)} per month).`; }
        return { status, label: { out: 'Out of stock', low: 'Low', adequate: 'Adequate' }[status], stock, reorder, coverage: coverage === null ? null : r1(coverage), why };
    }

    /* ---- window comparison: months inside a window vs all other months ---- */
    function windowStats(months, series, winMonths) {
        const inIdx = [], outIdx = [];
        months.forEach((ym, i) => (winMonths.includes(ymParts(ym).m) ? inIdx : outIdx).push(i));
        const inS = inIdx.map(i => series[i]), outS = outIdx.map(i => series[i]);
        const avgIn = mean(inS), avgOut = mean(outS);
        const ratio = avgOut > 0 ? avgIn / avgOut : (avgIn > 0 ? Infinity : 0);
        // distinct window instances (a Dec–Feb window spans two calendar years)
        const first = winMonths.find(m => !winMonths.includes(m === 1 ? 12 : m - 1));
        const inst = new Set();
        inIdx.forEach(i => { const { y, m } = ymParts(months[i]); inst.add(winMonths.includes(12) && m < first ? y - 1 : y); });
        return { avgIn: r1(avgIn), avgOut: r1(avgOut), ratio, inMonths: inIdx.length, outMonths: outIdx.length, unitsIn: sum(inS), instances: inst.size,
            elevated: inIdx.length >= CFG.minInWindow && outIdx.length >= CFG.minOutWindow && ratio >= CFG.seasonRatio };
    }

    function timing(winMonths, today) {
        const m = today.getMonth() + 1;
        if (winMonths.includes(m)) return { state: 'current', days: 0 };
        const first = winMonths.find(x => !winMonths.includes(x === 1 ? 12 : x - 1));
        let d = new Date(today.getFullYear(), first - 1, 1);
        if (d <= today) d = new Date(today.getFullYear() + 1, first - 1, 1);
        const days = Math.ceil((d - today) / 86400000);
        return { state: days <= CFG.approachingDays ? 'approaching' : 'later', days };
    }
    const timingPhrase = (t, range) => t.state === 'current' ? 'while this period is under way'
        : t.state === 'approaching' ? `as the ${range} period approaches (about ${plural(t.days, 'day')} away)` : `ahead of the next ${range} period`;

    /* ============================================================
       MAIN
       ============================================================ */
    function analyze(data, opts) {
        const start = opts.start, end = opts.end, today = opts.today || new Date();
        const months = completeMonths(start, end), n = months.length, idx = new Map(months.map((m, i) => [m, i]));
        const period = periodText(months, start, end);
        const items = (data.items || []);
        const byId = new Map(items.map(i => [i.item_id, i]));

        /* --- usage per item (totalAll = every dispense in range; series = complete months only) --- */
        const S = new Map(), T = new Map(), TX = new Map();
        (data.usage || []).forEach(u => {
            if (!byId.has(u.item_id)) return;
            T.set(u.item_id, (T.get(u.item_id) || 0) + u.qty);
            TX.set(u.item_id, (TX.get(u.item_id) || 0) + (u.n || 0));
            const i = idx.get(u.month); if (i === undefined) return;
            if (!S.has(u.item_id)) S.set(u.item_id, Array(n).fill(0));
            S.get(u.item_id)[i] += u.qty;
        });

        const metrics = items.filter(it => T.has(it.item_id) || it.active).map(it => {
            const series = S.get(it.item_id) || Array(n).fill(0), total = T.get(it.item_id) || 0;
            const active = series.filter(v => v > 0).length;
            return { id: it.item_id, name: it.name, category: it.category, unit: it.unit, isActive: it.active, expires: it.expires,
                series, total, transactions: TX.get(it.item_id) || 0, monthsActive: active, activeShare: n ? active / n : 0,
                avgMonthly: n ? r1(sum(series) / n) : 0, trend: trendOf(series, sum(series)), stock: stockOf(it, series), item: it };
        });
        const used = metrics.filter(m => m.total > 0).sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));

        /* --- rank + "consistently high demand" within each category --- */
        const catList = c => used.filter(m => m.category === c);
        ['Medicine', 'Medical Supply', 'Vaccine'].forEach(c => {
            const arr = catList(c), top = Math.max(1, Math.ceil(arr.length * 0.25));
            arr.forEach((m, i) => { m.rank = i + 1; m.consistentHigh = i < top && m.total >= CFG.minUnitsTrend && n >= 3 && m.activeShare >= CFG.consistentShare; });
        });
        used.forEach(m => { if (m.rank === undefined) { m.rank = 0; m.consistentHigh = false; } });

        /* --- vaccines administered (Immunization records) + matching inventory stock --- */
        const vacMap = new Map();
        (data.vaccines || []).forEach(v => {
            const e = vacMap.get(v.vaccine) || { name: v.vaccine, total: 0, series: Array(n).fill(0) };
            e.total += v.doses; const i = idx.get(v.month); if (i !== undefined) e.series[i] += v.doses; vacMap.set(v.vaccine, e);
        });
        const vaccineItems = items.filter(i => i.category === 'Vaccine' && i.active);
        const vaccines = [...vacMap.values()].map(v => {
            const nv = norm(v.name), hit = vaccineItems.filter(i => { const ni = norm(i.name); return ni && nv && (ni.includes(nv) || nv.includes(ni)); });
            const item = hit.length === 1 ? hit[0] : null;
            return Object.assign(v, { unit: 'doses', trend: trendOf(v.series, v.total), avgMonthly: n ? r1(sum(v.series) / n) : 0,
                stock: stockOf(item, v.series), item, monthsActive: v.series.filter(x => x > 0).length });
        }).sort((a, b) => b.total - a.total);

        /* --- disease case series --- */
        const dMap = new Map();
        (data.cases || []).forEach(c => {
            const e = dMap.get(c.disease) || { name: c.disease, total: 0, series: Array(n).fill(0) };
            e.total += c.cases; const i = idx.get(c.month); if (i !== undefined) e.series[i] += c.cases; dMap.set(c.disease, e);
        });
        const diseases = [...dMap.values()].map(d => Object.assign(d, { trend: trendOf(d.series, d.total) })).sort((a, b) => b.total - a.total);
        const dByName = new Map(diseases.map(d => [d.name, d]));

        /* --- disease links (actual dispensed prescriptions) --- */
        const linksByItem = new Map();
        (data.links || []).forEach(l => { (linksByItem.get(l.item_id) || linksByItem.set(l.item_id, []).get(l.item_id)).push(l); });
        const assoc = new Map();
        used.forEach(m => {
            const ls = (linksByItem.get(m.id) || []).sort((a, b) => b.rx - a.rx);
            const top = ls[0], d = top && dByName.get(top.disease);
            const r = d && n >= CFG.corrMinMonths ? pearson(m.series, d.series) : null;
            assoc.set(m.id, { diseases: ls.slice(0, 3), totalRx: sum(ls.map(l => l.rx)), topDisease: top ? top.disease : null,
                corr: r === null ? null : Math.round(r * 100) / 100, moves: r !== null && r >= CFG.corrMin && top.rx >= CFG.minRxLink });
        });

        /* --- sufficiency --- */
        const dispenseUnits = sum([...T.values()]);
        const histMonths = data.history && data.history.first && data.history.last
            ? ymRange(data.history.first.slice(0, 7), data.history.last.slice(0, 7)).length : 0;
        const suf = (() => {
            const notes = [];
            if (!dispenseUnits) return { level: 'none', months: n, histMonths, seasonalOk: false, notes, message: 'No dispensed medicines or supplies were recorded in the selected period, so usage cannot be analysed yet.' };
            let level = 'good', message = `${n} complete months of dispensing records support trend and seasonal comparison.`;
            if (n < CFG.minMonthsTrend) { level = 'limited'; message = `Only ${plural(n, 'complete month')} of records in this period. Usage trends need at least ${CFG.minMonthsTrend}; counts below are recorded totals only.`; }
            else if (n < CFG.minMonthsSeasonal) { level = 'moderate'; message = `${n} complete months of records. Usage trends are shown, but seasonal comparison needs at least ${CFG.minMonthsSeasonal} months.`; }
            if (n < CFG.minMonthsSeasonal && histMonths > n) notes.push(`Dispensing records exist for about ${histMonths} months in total. Widen the date range to include them.`);
            if (n >= CFG.minMonthsSeasonal && n < 24) notes.push('Less than two years of records: each calendar month is observed once, so seasonal patterns are marked as limited evidence.');
            return { level, months: n, histMonths, seasonalOk: n >= CFG.minMonthsSeasonal, notes, message };
        })();

        /* --- seasonal resource monitoring (cooler / rainy / warmer) --- */
        const catDefs = E.CONFIG.categories, seasons = E.CONFIG.seasons;
        const order = ['respiratory', 'vector', 'heat'];
        const cards = order.map(key => {
            const cat = catDefs[key], win = seasons[cat.season], t = timing(win.months, today);
            const ds = diseases.filter(d => cat.match.test(d.name)), dnames = new Set(ds.map(d => d.name));
            (data.links || []).forEach(l => { if (cat.match.test(l.disease)) dnames.add(l.disease); });
            const caseSeries = months.map((_, i) => sum(ds.map(d => d.series[i])));
            const caseTotal = sum(ds.map(d => d.total));
            const hs = windowStats(months, caseSeries, win.months);
            let hStatus, hDetail;
            if (!ds.length) { hStatus = 'no_cases'; hDetail = `No ${cat.label.toLowerCase()} cases were recorded in this period.`; }
            else if (!suf.seasonalOk) { hStatus = 'insufficient'; hDetail = `${caseTotal} recorded case${caseTotal === 1 ? '' : 's'} over ${plural(n, 'complete month')}. At least ${CFG.minMonthsSeasonal} months are needed to compare seasons.`; }
            else if (caseTotal < CFG.minCasesSeasonal) { hStatus = 'insufficient'; hDetail = `Only ${caseTotal} recorded case${caseTotal === 1 ? '' : 's'}, below the ${CFG.minCasesSeasonal} needed to compare seasons.`; }
            else if (hs.elevated) { hStatus = hs.instances >= 2 ? 'supported' : 'limited';
                hDetail = `Recorded cases averaged ${hs.avgIn} per month during ${win.range}, compared with ${hs.avgOut} per month in other months (${r1(hs.ratio)}×)${hs.instances >= 2 ? ` and this was seen in ${hs.instances} separate years` : ' (one observed year only)'}.`; }
            else { hStatus = 'none'; hDetail = `Recorded cases did not run clearly higher during ${win.range} (${hs.avgIn} per month vs ${hs.avgOut} in other months).`; }

            const bucket = { supported: [], linked: [], elevated: [] };
            if (suf.seasonalOk && ds.length) {
                used.forEach(m => {
                    const w = windowStats(months, m.series, win.months);
                    const rx = (linksByItem.get(m.id) || []).filter(l => dnames.has(l.disease)).reduce((a, l) => a + l.rx, 0);
                    const linked = rx >= CFG.minRxLink, high = w.elevated && w.unitsIn >= CFG.minUnitsSeasonal;
                    const row = { id: m.id, name: m.name, unit: m.unit, category: m.category, avgIn: w.avgIn, avgOut: w.avgOut, ratio: w.ratio, unitsIn: w.unitsIn, rx, stock: m.stock };
                    if (linked && high && (hStatus === 'supported' || hStatus === 'limited')) bucket.supported.push(row);
                    else if (linked) bucket.linked.push(row);
                    else if (high) bucket.elevated.push(row);
                });
                bucket.supported.sort((a, b) => b.unitsIn - a.unitsIn);
                bucket.linked.sort((a, b) => b.rx - a.rx);
                bucket.elevated.sort((a, b) => b.unitsIn - a.unitsIn);
            }

            const tp = timingPhrase(t, win.range), sup = bucket.supported.slice(0, CFG.maxListed).map(r => r.name);
            let insight;
            if (hStatus === 'no_cases') insight = key === 'heat'
                ? 'No heat-related cases (for example heat exhaustion or heat stroke) were recorded, so no resource pattern can be described. If the health center treats such cases, add them under Manage Diseases so they can be analysed.'
                : `No ${cat.label.toLowerCase()} cases were recorded, so no resource pattern can be described.`;
            else if (hStatus === 'insufficient') insight = `There are not enough records to compare ${win.range} with other months. Continue recording cases and dispensing to enable this analysis.`;
            else if (hStatus === 'none') insight = `The records do not show a clear rise in ${cat.label.toLowerCase()} cases during ${win.range}, so no seasonal resource pattern is identified from this data.`;
            else if (sup.length) insight = `Historical records show higher recorded ${cat.label.toLowerCase()} cases during ${win.range}, accompanied by higher dispensing of ${list(sup)}. These resources may require closer inventory monitoring ${tp}.`
                + (hStatus === 'limited' ? ' This is based on one observed year, so treat it as limited evidence.' : '');
            else insight = `Recorded ${cat.label.toLowerCase()} cases were higher during ${win.range}, but the inventory records do not show a matching rise in the dispensing of any item linked to these cases. No specific resource is identified from the records.`;
            if (key === 'heat') insight += ' No temperature data is integrated into the system, so this describes recorded cases only and does not state that any month will be hotter.';

            return { key, label: cat.label, season: cat.season, window: win, timing: t, diseases: [...dnames], caseTotal, caseSeries, health: Object.assign({ status: hStatus, detail: hDetail }, hs), bucket, insight };
        });

        /* --- section 5: demand-monitoring table --- */
        const HLABEL = { supported: 'Higher recorded cases', limited: 'Higher recorded cases (one year only)', none: 'No clear seasonal rise', insufficient: 'Not enough data', no_cases: 'No cases recorded' };
        const demand = cards.map(c => {
            const k = c.bucket.supported.length;
            return { period: `${c.window.range}`, label: c.window.label, concern: c.label, health: HLABEL[c.health.status],
                resource: k ? `Higher dispensing of ${k} linked item${k === 1 ? '' : 's'} in these months` : (c.health.status === 'supported' || c.health.status === 'limited') ? 'No linked item shows a matching rise' : 'No supported resource pattern',
                monitoring: k ? 'Monitor stock' : (c.health.status === 'insufficient' || c.health.status === 'no_cases') ? 'Insufficient records' : 'No seasonal monitoring identified', key: c.key };
        });

        /* --- summary lists --- */
        const top = c => used.find(m => m.category === c) || null;
        const summary = { topMedicine: top('Medicine'), topSupply: top('Medical Supply'), topVaccineItem: top('Vaccine'), topVaccineDose: vaccines[0] || null,
            increasing: used.filter(m => m.trend.status === 'increasing'), decreasing: used.filter(m => m.trend.status === 'decreasing'),
            consistent: used.filter(m => m.consistentHigh) };

        /* --- section 7: items requiring monitoring (combination of signals) --- */
        const inSeasonal = new Set();
        cards.forEach(c => { if (c.timing.state !== 'later') c.bucket.supported.forEach(r => inSeasonal.add(r.id)); });
        const rxUp = new Set();                                  // items linked to a disease whose recorded cases are rising
        used.forEach(m => (linksByItem.get(m.id) || []).forEach(l => { const d = dByName.get(l.disease); if (d && l.rx >= CFG.minRxLink && d.trend.status === 'increasing' && d.total >= CFG.minCasesTrend) rxUp.add(m.id); }));
        const alerts = used.map(m => {
            const sg = [];
            if (m.consistentHigh || (m.rank === 1 && m.total >= CFG.minUnitsTrend)) sg.push('High usage');
            if (m.trend.status === 'increasing') sg.push('Increasing usage');
            if (m.stock.status === 'low' || m.stock.status === 'out') sg.push(m.stock.status === 'out' ? 'Out of stock' : 'Low stock');
            if (m.monthsActive >= 3 && m.activeShare >= 0.5) sg.push('Repeated distribution');
            if (inSeasonal.has(m.id)) sg.push('Seasonal demand pattern');
            if (rxUp.has(m.id)) sg.push('Linked disease trend rising');
            const low = m.stock.status === 'low' || m.stock.status === 'out';
            const strong = sg.filter(x => x !== 'Repeated distribution').length;
            const level = low && strong >= 2 ? 'priority' : strong >= 2 ? 'watch' : null;
            return level ? { id: m.id, name: m.name, category: m.category, unit: m.unit, level, signals: sg, m } : null;
        }).filter(Boolean).sort((a, b) => (a.level === 'priority' ? 0 : 1) - (b.level === 'priority' ? 0 : 1) || b.signals.length - a.signals.length || b.m.total - a.m.total);

        const itemText = m => {
            const parts = [`${m.name} was distributed in ${m.monthsActive} of ${n} complete months, with ${m.total} ${m.unit} dispensed in ${period}.`];
            if (m.trend.status === 'increasing') parts.push(`Monthly usage rose from ${m.trend.prior} to ${m.trend.recent} ${m.unit} (last 3 months vs the 3 before).`);
            else if (m.trend.status === 'decreasing') parts.push(`Monthly usage fell from ${m.trend.prior} to ${m.trend.recent} ${m.unit} (last 3 months vs the 3 before).`);
            parts.push(`Current stock: ${m.stock.label.toLowerCase()} (${m.stock.stock} ${m.unit}). ${m.stock.why}`);
            return parts.join(' ');
        };
        alerts.forEach(a => { a.insight = `${itemText(a.m)} Based on the available inventory and historical usage records, this item should be monitored closely.`; });

        used.forEach(m => {
            if (m.consistentHigh) m.insight = `The recorded usage shows consistently high demand for ${m.name} during ${period}. The inventory should be monitored regularly to reduce the possibility of insufficient stock.`
                + (m.stock.status === 'low' || m.stock.status === 'out' ? ` Current stock is ${m.stock.label.toLowerCase()}.` : '');
            else m.insight = itemText(m);
        });

        /* --- chart helper data --- */
        const catUsage = ['Medicine', 'Vaccine', 'Medical Supply'].map(c => ({ category: c, series: months.map((_, i) => sum(used.filter(m => m.category === c).map(m => m.series[i]))) }));
        const calendar = m => { const a = Array(12).fill(0), c = Array(12).fill(0); months.forEach((ym, i) => { const k = ymParts(ym).m - 1; a[k] += m.series[i]; c[k]++; }); return a.map((v, k) => c[k] ? r1(v / c[k]) : null); };

        return { range: { start, end }, months, monthLabels: months.map(ymLabel), period, sufficiency: suf, items: metrics, used, vaccines, diseases, assoc, summary, cards, demand, alerts, catUsage, calendar,
            totals: { units: dispenseUnits, itemsUsed: used.length, doses: sum(vaccines.map(v => v.total)) } };
    }

    return { analyze, CFG, MN, MFULL, ymLabel, completeMonths, periodText, trendOf, stockOf, windowStats, pearson, timing };
});
