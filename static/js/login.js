/**
 * Laundry Lounge — Login Page JavaScript
 * login.js
 *
 * Handles:
 *  - Theme toggle (persisted in localStorage)
 *  - Remember Me (restore identifier on load)
 *  - Client-side attempt warning + lockout display countdown
 *  - Form submission with loading state
 *  - Redirect overlay
 *  - Forgot Password modal (POST to /forgot-password)
 *  - Customer Registration modal (POST to /register)
 *  - Flash message auto-dismiss
 *  - Accessibility (Escape key, backdrop click)
 *
 * NOTE: All actual security (attempt tracking, lockout, password
 * hashing) lives in app.py. This file only manages UI state and
 * progressive enhancement.
 */

'use strict';

/* ════════════════════════════════════════════════════
   THEME
════════════════════════════════════════════════════ */

function toggleTheme() {
  const html   = document.documentElement;
  const isDark = html.dataset.theme === 'dark';
  html.dataset.theme = isDark ? 'light' : 'dark';
  document.getElementById('themeToggle').textContent = isDark ? '☀' : '☽';
  localStorage.setItem('ll-theme', html.dataset.theme);
}

(function initTheme() {
  const saved = localStorage.getItem('ll-theme');
  if (saved) {
    document.documentElement.dataset.theme = saved;
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = saved === 'dark' ? '☽' : '☀';
  }
})();


/* ════════════════════════════════════════════════════
   REMEMBER ME  — restore saved identifier
════════════════════════════════════════════════════ */

(function initRemember() {
  const saved = localStorage.getItem('ll-remember');
  if (!saved) return;
  try {
    const d = JSON.parse(saved);
    const inp = document.getElementById('identifier');
    const chk = document.getElementById('rememberMe');
    if (inp && d.identifier) {
      inp.value   = d.identifier;
      chk.checked = true;
    }
  } catch (_) {}
})();

function persistRemember(identifier) {
  localStorage.setItem('ll-remember', JSON.stringify({ identifier }));
}
function clearRemember() {
  localStorage.removeItem('ll-remember');
}


/* ════════════════════════════════════════════════════
   CLIENT-SIDE ATTEMPT DISPLAY
   (Server is authoritative — this is purely cosmetic)
════════════════════════════════════════════════════ */

const MAX_ATTEMPTS  = 5;
const LOCKOUT_MS    = 5 * 60 * 1000;
const WARN_AFTER    = 3;
let   lockoutTimerID = null;

function getAttemptData() {
  try {
    return JSON.parse(localStorage.getItem('ll-attempts') || '{"count":0,"lockedUntil":0}');
  } catch (_) {
    return { count: 0, lockedUntil: 0 };
  }
}
function saveAttemptData(d) {
  localStorage.setItem('ll-attempts', JSON.stringify(d));
}
function isLockedOut() {
  const d = getAttemptData();
  return (d.lockedUntil && Date.now() < d.lockedUntil) ? d.lockedUntil : false;
}
function incrementAttempt() {
  const d   = getAttemptData();
  d.count   = (d.count || 0) + 1;
  if (d.count >= MAX_ATTEMPTS) d.lockedUntil = Date.now() + LOCKOUT_MS;
  saveAttemptData(d);
  return d;
}
function clearAttempts() {
  saveAttemptData({ count: 0, lockedUntil: 0 });
}

function startLockoutDisplay(until) {
  const bar     = document.getElementById('lockoutBar');
  const timerEl = document.getElementById('lockoutTimer');
  const btn     = document.getElementById('submitBtn');
  if (!bar || !timerEl || !btn) return;

  bar.classList.add('visible');
  btn.disabled = true;

  if (lockoutTimerID) clearInterval(lockoutTimerID);
  lockoutTimerID = setInterval(() => {
    const rem = until - Date.now();
    if (rem <= 0) {
      clearInterval(lockoutTimerID);
      bar.classList.remove('visible');
      const warn = document.getElementById('attemptWarn');
      if (warn) warn.classList.remove('visible');
      btn.disabled = false;
      clearAttempts();
      return;
    }
    const m = Math.floor(rem / 60000);
    const s = Math.floor((rem % 60000) / 1000);
    timerEl.textContent = `${m}:${s.toString().padStart(2, '0')}`;
  }, 500);
}

// Check lockout on page load
(function checkLockout() {
  const until = isLockedOut();
  if (until) startLockoutDisplay(until);
})();


/* ════════════════════════════════════════════════════
   LOGIN FORM  — loading state + client-side feedback
════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {

  const form = document.getElementById('loginForm');
  if (form) {
    form.addEventListener('submit', handleLoginSubmit);
  }

  // Clear field error on input
  ['identifier', 'password'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => el.classList.remove('error'));
  });

});

async function handleLoginSubmit(e) {
  // Do NOT e.preventDefault() — let Flask handle the POST.
  // We only show the loading spinner and persist Remember Me.

  const btn        = document.getElementById('submitBtn');
  const identifier = document.getElementById('identifier');
  const password   = document.getElementById('password');
  const rememberMe = document.getElementById('rememberMe');

  // Client-side lockout guard
  const until = isLockedOut();
  if (until) {
    e.preventDefault();
    startLockoutDisplay(until);
    return;
  }

  // Basic empty-field check (HTML5 `required` handles this too)
  if (!identifier?.value.trim() || !password?.value) {
    // Let the browser's native validation show
    return;
  }

  // Persist Remember Me before submit
  if (rememberMe?.checked) {
    persistRemember(identifier.value.trim());
  } else {
    clearRemember();
  }

  // Show spinner
  if (btn) {
    btn.classList.add('loading');
    btn.disabled = true;
  }
}

/* Show client-side attempt warning after a failed response
   (Flask flashes the real message; this adds a visible warn
   indicator for JS-enhanced UX) */
(function checkFlashForAttemptWarn() {
  // If Flask flashed an "Incorrect credentials" message,
  // bump the client-side counter too.
  const flashEls = document.querySelectorAll('.flash-msg.error');
  flashEls.forEach(el => {
    const text = el.textContent.toLowerCase();
    if (text.includes('incorrect') || text.includes('invalid')) {
      const d         = incrementAttempt();
      const lockedUntil = isLockedOut();
      if (lockedUntil) {
        startLockoutDisplay(lockedUntil);
        return;
      }
      const remaining = MAX_ATTEMPTS - d.count;
      if (d.count >= WARN_AFTER) {
        const warn = document.getElementById('attemptWarn');
        const wt   = document.getElementById('attemptWarnText');
        if (warn && wt) {
          wt.textContent = remaining > 0
            ? `${remaining} attempt${remaining !== 1 ? 's' : ''} remaining before lockout.`
            : 'Account will be locked after this attempt.';
          warn.classList.add('visible');
        }
      }
    }
    if (text.includes('locked')) {
      const until = isLockedOut();
      if (until) startLockoutDisplay(until);
    }
  });
})();


/* ════════════════════════════════════════════════════
   PASSWORD TOGGLE
════════════════════════════════════════════════════ */

function togglePwd() {
  const p = document.getElementById('password');
  if (!p) return;
  p.type = p.type === 'password' ? 'text' : 'password';
}


/* ════════════════════════════════════════════════════
   FORGOT PASSWORD MODAL
════════════════════════════════════════════════════ */

function openForgot() {
  openOverlay('forgotModal');
  setTimeout(() => {
    const inp = document.getElementById('resetEmail');
    if (inp) inp.focus();
  }, 350);
}

function submitReset() {
  const email = document.getElementById('resetEmail')?.value.trim();
  const inp   = document.getElementById('resetEmail');

  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    if (inp) {
      inp.style.borderColor = '#DC3C3C';
      inp.style.boxShadow   = '0 0 0 3px rgba(220,60,60,0.15)';
    }
    return;
  }

  // Submit via native form POST to /forgot-password
  const form    = document.createElement('form');
  form.method   = 'POST';
  form.action   = '/forgot-password';

  const csrfInput = document.querySelector('input[name="csrf_token"]');
  if (csrfInput) {
    const c   = document.createElement('input');
    c.type    = 'hidden';
    c.name    = 'csrf_token';
    c.value   = csrfInput.value;
    form.appendChild(c);
  }

  const emailInput = document.createElement('input');
  emailInput.type  = 'hidden';
  emailInput.name  = 'email';
  emailInput.value = email;
  form.appendChild(emailInput);
  document.body.appendChild(form);
  form.submit();
}


/* ════════════════════════════════════════════════════
   CUSTOMER REGISTER MODAL
════════════════════════════════════════════════════ */

function openRegister() {
  openOverlay('registerModal');
}

function submitRegister() {
  const first    = document.getElementById('regFirst')?.value.trim();
  const last     = document.getElementById('regLast')?.value.trim();
  const email    = document.getElementById('regEmail')?.value.trim();
  const phone    = document.getElementById('regPhone')?.value.trim();
  const password = document.getElementById('regPassword')?.value;
  const confirm  = document.getElementById('regConfirm')?.value;

  if (!first || !last || !email || !password || !confirm) {
    showFormError('regForm', 'Please fill in all required fields.');
    return;
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    showFormError('regForm', 'Please enter a valid email address.');
    return;
  }
  if (password.length < 8) {
    showFormError('regForm', 'Password must be at least 8 characters.');
    return;
  }
  if (password !== confirm) {
    showFormError('regForm', 'Passwords do not match.');
    return;
  }

  // Build and submit form to /register
  const form     = document.createElement('form');
  form.method    = 'POST';
  form.action    = '/register';

  const fields = {
    first_name: first,
    last_name:  last,
    email:      email,
    password:   password,
    confirm:    confirm,
  };
  if (phone) fields.phone = phone;

  const csrfInput = document.querySelector('input[name="csrf_token"]');
  if (csrfInput) fields.csrf_token = csrfInput.value;

  Object.entries(fields).forEach(([name, value]) => {
    const inp   = document.createElement('input');
    inp.type    = 'hidden';
    inp.name    = name;
    inp.value   = value;
    form.appendChild(inp);
  });

  document.body.appendChild(form);
  form.submit();
}


/* ════════════════════════════════════════════════════
   OVERLAY / MODAL HELPERS
════════════════════════════════════════════════════ */

function openOverlay(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('active');
}

function closeOverlay(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('active');

  // Reset error modal icon after close
  if (id === 'errorModal') {
    const icon = el.querySelector('.modal-icon');
    const btn  = el.querySelector('.modal-close');
    if (icon) icon.textContent = '⚠️';
    if (btn) { btn.classList.remove('green'); btn.classList.add('red'); }
  }
}

// Close on backdrop click
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.overlay').forEach(el => {
    el.addEventListener('click', function (e) {
      if (e.target === this) closeOverlay(this.id);
    });
  });
});

// Close on Escape key
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.overlay.active').forEach(el => {
      if (el.id !== 'redirectOverlay') closeOverlay(el.id);
    });
  }
});

function showFormError(containerId, msg) {
  const errId    = `${containerId}-err`;
  let existing   = document.getElementById(errId);
  if (!existing) {
    existing           = document.createElement('p');
    existing.id        = errId;
    existing.style.cssText =
      'font-size:0.74rem;color:#DC3C3C;text-align:center;margin-bottom:4px;' +
      'font-family:"DM Mono",monospace;letter-spacing:0.05em;';
    const container = document.getElementById(containerId);
    if (container) container.insertAdjacentElement('afterend', existing);
  }
  existing.textContent = msg;
  setTimeout(() => { if (existing) existing.textContent = ''; }, 3500);
}


/* ════════════════════════════════════════════════════
   FLASH MESSAGE AUTO-DISMISS
════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.flash-msg').forEach((msg, i) => {
    // Auto-dismiss after 5s (staggered)
    setTimeout(() => dismissFlash(msg), 5000 + i * 500);
  });
});

function dismissFlash(el) {
  if (!el) return;
  el.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
  el.style.opacity    = '0';
  el.style.transform  = 'translateX(20px)';
  setTimeout(() => el.remove(), 350);
}
