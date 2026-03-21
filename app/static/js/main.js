/* =============================================
   Dubai Stem Cell Congress 2026 — Frontend JS
   ============================================= */

// Toast notification
function showToast(message, type = 'success') {
  // Remove existing
  document.querySelectorAll('.toast').forEach(t => t.remove());

  const toast = document.createElement('div');
  toast.className = `toast toast-${type} show`;
  toast.innerHTML = `<span>${type === 'success' ? '✓' : '✕'}</span> ${message}`;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-12px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// API helper
async function api(url, data) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'same-origin',
    });

    const json = await res.json();

    if (!res.ok) {
      const msg = json.detail;
      if (Array.isArray(msg)) {
        // Pydantic validation errors
        throw new Error(msg.map(e => e.msg).join('. '));
      }
      throw new Error(msg || 'Something went wrong');
    }

    return json;
  } catch (err) {
    throw err;
  }
}

// Chip toggle (research interests)
document.addEventListener('click', (e) => {
  if (e.target.closest('.chip')) {
    e.target.closest('.chip').classList.toggle('selected');
  }
});

// Get selected chips
function getSelectedChips(container) {
  return Array.from(container.querySelectorAll('.chip.selected'))
    .map(c => c.dataset.value);
}

// Animate elements on scroll / load
function observeAnimations() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('animate-up');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1 });

  document.querySelectorAll('.observe').forEach(el => observer.observe(el));
}

document.addEventListener('DOMContentLoaded', observeAnimations);
