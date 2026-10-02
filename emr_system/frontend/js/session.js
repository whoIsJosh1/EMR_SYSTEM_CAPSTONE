/* ============================================================
   session.js - Session Timeout & Screen Lock Management (v2)

   Mga pagbabago sa v2:
   - Naka-PERSIST na ang lock state (sessionStorage 'session_locked'),
     kaya hindi na nababypass ng page refresh / F5.
   - HINDI na nire-reset ang last_activity sa page load. Kapag lampas
     na sa timeout habang naka-refresh/naka-sleep ang PC, lock agad.
   - Timestamp-based na ang idle check (iisang 1s tick) imbes na
     setTimeout, kaya hindi na naaapektuhan ng browser throttling
     o sleep/hibernate.
   - Naayos ang mga ID mismatch sa dashboard.html
     (unlockPwd, toast-container) at ang unlockSession() na
     talagang ginagamit na ng Unlock button.
   - May attempt limit sa unlock, at naka-`inert` ang background
     habang naka-lock (hindi na matatabbing/ma-type-an sa likod).
   - Back-button (bfcache) pagkatapos mag-logout: ire-redirect na.
   - Unlock gamit ang /api/auth/verify-password (hindi na bagong LOGIN),
     at silent token refresh bawat 10 min habang aktibo ang user.
   - Auto logout 15 min pagkatapos mag-lock (AUTO_LOGOUT_AFTER_LOCK_MS),
     naka-redirect sa /?reason=timeout | expired | attempts | locked.
   ============================================================ */
(function () {
    'use strict';

    // ---- Configuration ----
    const SESSION_TIMEOUT_MS   = 10 * 60 * 1000;  // 10 minuto ng inactivity
    const WARNING_BEFORE_MS    = 2  * 60 * 1000;  // Warning 2 minuto bago mag-lock
    const TICK_MS              = 1000;            // Idle check bawat 1 segundo
    const ACTIVITY_THROTTLE_MS = 5000;            // Max na pag-save ng activity sa storage
    const MAX_UNLOCK_ATTEMPTS  = 5;               // Lampas dito = forced logout (dapat < MAX_FAILED_ATTEMPTS ng server, default 5)
    const AUTO_LOGOUT_AFTER_LOCK_MS = 5 * 60 * 1000; // Auto logout 5 min pagkatapos mag-lock
    const REFRESH_EVERY_MS     = 10 * 60 * 1000;  // Silent token refresh bawat 10 min (habang aktibo)
    const REFRESH_RETRY_MS     = 60 * 1000;       // Retry kung pumalya ang refresh

    // Storage keys
    const K = {
        token:   'access_token',
        expires: 'token_expires',
        last:    'last_activity',
        locked:  'session_locked',
        email:   'user_email',
        refreshed: 'token_refreshed_at'
    };

    let sessionLockActive = false;
    let lastActivity      = 0;   // in-memory, ito ang binabasa ng tick
    let lastPersist       = 0;
    let unlockAttempts    = 0;
    let lastRefresh       = 0;
    let refreshing        = false;
    let refreshDisabled   = false;   // True kapag lampas na sa MAX_SESSION_HOURS ng server

    const $ = id => document.getElementById(id);
    const hasToken = () => !!sessionStorage.getItem(K.token);
    const getNum   = k => parseInt(sessionStorage.getItem(k) || '0', 10) || 0;
    const toast    = (msg, type, ms) => {
        if (typeof showToast === 'function') showToast(msg, type, ms);
    };

    /* ---------- Logout helpers ---------- */

    // Best-effort logout sa server (para may LOGOUT sa audit log), then balik sa login.
    function forceLogout(reason) {
        const t = sessionStorage.getItem(K.token);
        try {
            if (t) {
                fetch(`${window.location.origin}/api/auth/logout`, {
                    method:    'POST',
                    headers:   { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t },
                    body:      '{}',
                    keepalive: true
                }).catch(() => {});
            }
        } catch (_) {}
        sessionStorage.clear();
        window.location.href = reason ? '/?reason=' + encodeURIComponent(reason) : '/';
    }

    function expireSession() {
        sessionStorage.clear();
        toast('Your session has expired. Please login again.', 'warning');
        setTimeout(() => { window.location.href = '/?reason=expired'; }, 2000);
    }

    /* ---------- Activity tracking ---------- */

    function markActivity(force) {
        if (sessionLockActive) return;            // walang extension habang naka-lock
        const now = Date.now();
        lastActivity = now;
        if (force || now - lastPersist > ACTIVITY_THROTTLE_MS) {
            sessionStorage.setItem(K.last, String(now));
            lastPersist = now;
        }
    }

    // Compatibility: dating pangalan ng function
    function resetSessionTimer() { markActivity(true); }

    /* ---------- Warning toast ---------- */

    function showWarning(msLeft) {
        let toastEl = $('sessionWarningToast');
        if (!toastEl) {
            const container = $('toast-container');
            if (!container) return;

            toastEl = document.createElement('div');
            toastEl.id        = 'sessionWarningToast';
            toastEl.className = 'toast warning';
            toastEl.style.cssText = 'min-width:320px;';

            const icon = document.createElement('span');
            icon.style.fontSize = '20px';
            icon.textContent = '⏰';

            const body  = document.createElement('div');
            body.style.flex = '1';
            const title = document.createElement('div');
            title.style.cssText = 'font-weight:600;margin-bottom:3px;';
            title.textContent = 'Session Expiring Soon';
            const count = document.createElement('div');
            count.id = 'warningCountdown';
            count.style.cssText = 'font-size:12px;color:var(--muted);';
            body.append(title, count);

            const btn = document.createElement('button');
            btn.textContent = 'Stay Active';
            btn.style.cssText = 'background:var(--blue);color:#fff;border:none;border-radius:7px;' +
                                'padding:6px 12px;font-size:12px;cursor:pointer;font-weight:600;';
            btn.addEventListener('click', extendSession);

            toastEl.append(icon, body, btn);
            container.appendChild(toastEl);
        }
        const secs = Math.max(0, Math.ceil(msLeft / 1000));
        const el   = $('warningCountdown');
        if (el) el.textContent = `Screen will lock in ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
    }

    function hideWarning() {
        const el = $('sessionWarningToast');
        if (el) el.remove();
    }

    function extendSession() {
        hideWarning();
        markActivity(true);
        toast("Session extended. You're still logged in.", 'success', 2000);
    }

    /* ---------- Silent token refresh ----------
       Para hindi ma-logout ang AKTIBONG user sa dulo ng token lifetime (60 min).
       Nagre-refresh lang kung may activity mula noong huling refresh. Ang server
       ang may hawak ng absolute limit (MAX_SESSION_HOURS) — pagkatapos nun, 401
       at hahayaan na lang nating mag-expire ang token. */

    function setRefreshed(ts) {
        lastRefresh = ts;
        sessionStorage.setItem(K.refreshed, String(ts));
    }

    async function refreshToken() {
        const t = sessionStorage.getItem(K.token);
        if (!t || refreshing) return;
        refreshing = true;
        try {
            const res = await fetch(`${window.location.origin}/api/auth/refresh`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t },
                body:    '{}'
            });
            if (res.ok) {
                const d = await res.json();
                // hasToken(): kung nag-logout habang naghihintay, huwag ibalik ang token
                if (d.access_token && hasToken()) {
                    sessionStorage.setItem(K.token,   d.access_token);
                    sessionStorage.setItem(K.expires, String(Date.now() + d.expires_in * 1000));
                }
                setRefreshed(Date.now());
            } else if (res.status === 401) {
                refreshDisabled = true;      // session limit na — hayaang mag-expire
            } else {
                setRefreshed(Date.now() - REFRESH_EVERY_MS + REFRESH_RETRY_MS);
            }
        } catch (_) {
            setRefreshed(Date.now() - REFRESH_EVERY_MS + REFRESH_RETRY_MS);
        } finally {
            refreshing = false;
        }
    }

    /* ---------- Lock / Unlock ---------- */

    // I-disable ang interaction sa lahat ng nasa likod ng lock overlay.
    function setBackgroundInert(on) {
        Array.from(document.body.children).forEach(el => {
            if (el.id === 'sessionLock' || el.id === 'toast-container') return;
            if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') return;
            if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert');
        });
    }

    function lockScreen() {
        if (!hasToken()) return;                       // walang session, walang ila-lock

        sessionLockActive = true;
        sessionStorage.setItem(K.locked, '1');         // <-- PERSIST: survive ang refresh
        hideWarning();
        setBackgroundInert(true);

        const overlay = $('sessionLock');
        if (overlay) {
            overlay.classList.add('show');
            setTimeout(() => {
                const pw = $('unlockPwd');
                if (pw) { pw.value = ''; pw.focus(); }
            }, 200);
        }
    }

    function showUnlockError(msg) {
        const el = $('unlockError');
        if (!el) return;
        el.textContent = '⚠️ ' + msg;
        el.style.display = 'block';
    }

    async function unlockSession() {
        const pwField = $('unlockPwd');
        const btn     = document.querySelector('.btn-unlock');

        if (!pwField || !pwField.value) { showUnlockError('Please enter your password.'); return; }

        const tok = sessionStorage.getItem(K.token);
        if (!tok) { forceLogout('expired'); return; }

        if (btn) btn.disabled = true;                  // iwas double-submit
        try {
            // Vine-verify ang password ng KASALUKUYANG user (hindi bagong login)
            const res = await fetch(`${window.location.origin}/api/auth/verify-password`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok },
                body:    JSON.stringify({ password: pwField.value })
            });
            let data = {};
            try { data = await res.json(); } catch (_) {}

            if (res.ok) {
                if (data.access_token) {
                    sessionStorage.setItem(K.token,   data.access_token);
                    sessionStorage.setItem(K.expires, String(Date.now() + data.expires_in * 1000));
                }
                sessionStorage.removeItem(K.locked);

                sessionLockActive = false;
                unlockAttempts    = 0;
                refreshDisabled   = false;
                setRefreshed(Date.now());
                setBackgroundInert(false);

                const overlay = $('sessionLock');
                if (overlay) overlay.classList.remove('show');
                const errEl = $('unlockError');
                if (errEl) errEl.style.display = 'none';
                pwField.value = '';

                markActivity(true);                    // bagong idle window
                toast('Session unlocked. Welcome back!', 'success', 2000);
            } else if (res.status === 423 || res.status === 403) {
                forceLogout('locked');                 // naka-lock / deactivated na ang account sa server
                return;
            } else if (res.status === 401) {
                forceLogout('expired');                // expired na ang token habang naka-lock
                return;
            } else if (res.status === 429) {
                showUnlockError('Too many requests. Please wait a moment and try again.');
            } else {
                unlockAttempts++;
                if (unlockAttempts >= MAX_UNLOCK_ATTEMPTS) { forceLogout('attempts'); return; }

                const detail = (typeof data.detail === 'string') ? data.detail : '';
                showUnlockError(detail || 'Incorrect password. Please try again.');
                pwField.value = '';
                pwField.focus();

                const card = document.querySelector('.lock-card');
                if (card) {
                    card.style.animation = 'none';
                    void card.offsetHeight;
                    card.style.animation = 'lockShake 0.4s ease';
                }
            }
        } catch (_) {
            showUnlockError('Cannot connect to server. Please try again.');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    /* ---------- Main tick (timestamp-based) ---------- */

    function sessionTick() {
        if (!hasToken()) return;

        // Naka-lock na: kung lampas na sa grace period, auto logout.
        // (Hindi nag-a-update ang lastActivity habang naka-lock, kaya tuloy ang bilang.)
        if (sessionLockActive) {
            if (Date.now() - lastActivity >= SESSION_TIMEOUT_MS + AUTO_LOGOUT_AFTER_LOCK_MS) {
                forceLogout('timeout');
            }
            return;
        }

        const now = Date.now();

        // 1) Token expiry (kung walang token_expires, ituturing na expired - same sa dati)
        if (now > getNum(K.expires)) { expireSession(); return; }

        // 2) Inactivity
        const idle = now - lastActivity;
        if (idle >= SESSION_TIMEOUT_MS) { lockScreen(); return; }

        if (idle >= SESSION_TIMEOUT_MS - WARNING_BEFORE_MS) showWarning(SESSION_TIMEOUT_MS - idle);
        else hideWarning();

        // 3) Silent refresh: may activity mula noong huling refresh at lampas na sa 10 min
        if (!refreshing && !refreshDisabled &&
            now - lastRefresh >= REFRESH_EVERY_MS && lastActivity > lastRefresh) {
            refreshToken();
        }
    }

    /* ---------- Event wiring ---------- */

    ['mousemove', 'mousedown', 'keydown', 'touchstart', 'touchmove', 'scroll', 'click', 'wheel']
        .forEach(ev => document.addEventListener(ev, () => markActivity(false),
                                                 { passive: true, capture: true }));

    // Pagbalik sa tab / paggising ng PC: i-check agad, huwag hintayin ang susunod na tick
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') sessionTick();
    });

    // Back button pagkatapos mag-logout (bfcache): huwag ipakita ang lumang dashboard
    window.addEventListener('pageshow', e => {
        if (e.persisted) {
            if (!hasToken()) { window.location.replace('/'); return; }
            sessionTick();
        }
    });

    /* ---------- Init ---------- */

    // Email para sa unlock verification
    const cu = (typeof getCurrentUser === 'function') ? getCurrentUser() : null;
    if (cu && cu.email) {
        sessionStorage.setItem(K.email, cu.email);
    } else if (typeof apiGet === 'function') {
        apiGet('/api/auth/me').then(u => { if (u) sessionStorage.setItem(K.email, u.email); }).catch(() => {});
    }

    // IMPORTANTE: i-restore ang last_activity — HUWAG i-reset sa page load.
    const saved = getNum(K.last);
    lastActivity = saved || Date.now();
    lastPersist  = lastActivity;
    if (!saved) sessionStorage.setItem(K.last, String(lastActivity));

    lastRefresh = getNum(K.refreshed) || Date.now();     // walang tala = kaka-login lang
    sessionStorage.setItem(K.refreshed, String(lastRefresh));

    if (sessionStorage.getItem(K.locked) === '1') { lockScreen(); sessionTick(); } // naka-lock bago mag-refresh; lampas na ba sa auto logout?
    else sessionTick();                                                           // lampas na ba sa timeout?

    setInterval(sessionTick, TICK_MS);

    // Shake animation
    const style = document.createElement('style');
    style.textContent = `
@keyframes lockShake {
    0%,100% { transform: translateX(0); }
    20%     { transform: translateX(-8px); }
    40%     { transform: translateX(8px); }
    60%     { transform: translateX(-5px); }
    80%     { transform: translateX(5px); }
}`;
    document.head.appendChild(style);

    // Public API (ginagamit ng HTML onclick / ibang scripts)
    window.lockScreen         = lockScreen;
    window.unlockSession      = unlockSession;
    window.extendSession      = extendSession;
    window.resetSessionTimer  = resetSessionTimer;
    window.logoutFromLock     = () => forceLogout();
    window.isSessionLocked    = () => sessionLockActive;
})();