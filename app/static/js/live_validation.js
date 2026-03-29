(function () {
  function wireLiveValidation() {
    const usernameInput = document.getElementById('usernameInput');
    const usernameFeedback = document.getElementById('usernameFeedback');
    const emailInput = document.getElementById('emailInput');
    const emailFeedback = document.getElementById('emailFeedback');
    if (!usernameInput && !emailInput) return;

    let usernameTimer = null;
    let emailTimer = null;

    function clearFeedback(el) {
      if (!el) return;
      el.textContent = '';
      el.className = 'form-text';
    }

    function renderUsernameState(data) {
      if (!usernameFeedback) return;
      clearFeedback(usernameFeedback);
      if (!data || !data.username) return;
      if (data.available) {
        usernameFeedback.textContent = 'Username is available.';
        usernameFeedback.className = 'form-text text-success';
        return;
      }
      usernameFeedback.className = 'form-text text-warning';
      usernameFeedback.append(document.createTextNode('Username already exists. Suggested alternative: '));
      if (data.suggestion) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-link btn-sm p-0 align-baseline';
        btn.id = 'useSuggestedUsername';
        btn.textContent = data.suggestion;
        btn.addEventListener('click', function () {
          usernameInput.value = data.suggestion;
          checkUsernameAvailability();
        });
        usernameFeedback.appendChild(btn);
      }
    }

    function renderEmailState(data) {
      if (!emailFeedback) return;
      clearFeedback(emailFeedback);
      if (!data || !data.email) return;
      if (data.available) {
        emailFeedback.textContent = 'Email address is available.';
        emailFeedback.className = 'form-text text-success';
        return;
      }
      if (data.exists) {
        emailFeedback.className = 'form-text text-warning';
        emailFeedback.append(document.createTextNode('Email address already exists. '));
        if (data.forgot_password_url) {
          const link = document.createElement('a');
          link.href = data.forgot_password_url;
          link.textContent = 'Reset password';
          emailFeedback.appendChild(link);
        }
      }
    }

    async function checkUsernameAvailability() {
      if (!usernameInput || !usernameFeedback) return;
      const value = usernameInput.value.trim();
      if (!value) {
        clearFeedback(usernameFeedback);
        return;
      }
      try {
        const res = await fetch(`/api/check-username?username=${encodeURIComponent(value)}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'same-origin'
        });
        if (!res.ok) {
          clearFeedback(usernameFeedback);
          return;
        }
        const data = await res.json();
        renderUsernameState(data);
      } catch (_err) {
        clearFeedback(usernameFeedback);
      }
    }

    async function checkEmailAvailability() {
      if (!emailInput || !emailFeedback) return;
      const value = emailInput.value.trim();
      if (!value) {
        clearFeedback(emailFeedback);
        return;
      }
      try {
        const res = await fetch(`/api/check-email?email=${encodeURIComponent(value)}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'same-origin'
        });
        if (!res.ok) {
          clearFeedback(emailFeedback);
          return;
        }
        const data = await res.json();
        renderEmailState(data);
      } catch (_err) {
        clearFeedback(emailFeedback);
      }
    }

    if (usernameInput) {
      usernameInput.addEventListener('input', function () {
        window.clearTimeout(usernameTimer);
        usernameTimer = window.setTimeout(checkUsernameAvailability, 250);
      });
      usernameInput.addEventListener('blur', checkUsernameAvailability);
      checkUsernameAvailability();
    }

    if (emailInput) {
      emailInput.addEventListener('input', function () {
        window.clearTimeout(emailTimer);
        emailTimer = window.setTimeout(checkEmailAvailability, 250);
      });
      emailInput.addEventListener('blur', checkEmailAvailability);
      checkEmailAvailability();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireLiveValidation);
  } else {
    wireLiveValidation();
  }
})();
