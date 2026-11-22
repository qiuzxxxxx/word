(function () {
  const flow = document.getElementById('webLoginPopups');
  if (!flow) return;
  const guide = document.getElementById('webUsageGuide');
  const closeGuide = document.getElementById('webUsageGuideClose');

  async function showNextPopup() {
    try {
      const response = await fetch(flow.dataset.url, {
        method: 'POST',
        headers: { 'X-CSRFToken': flow.dataset.csrf },
      });
      if (!response.ok) return;
      const { popup } = await response.json();
      if (popup === 'agreement') {
        window.openPrivacy(showNextPopup);
      } else if (popup === 'guide') {
        guide.classList.remove('hidden');
        closeGuide.focus();
      } else if (popup === 'announcement' && window.openAnnouncementModal) {
        window.openAnnouncementModal('latest');
      }
    } catch (error) {
      console.warn('Could not load the next login popup.', error);
    }
  }

  if (guide) {
    function dismissGuide() {
      if (guide.classList.contains('hidden')) return;
      guide.classList.add('hidden');
      showNextPopup();
    }

    closeGuide.addEventListener('click', dismissGuide);
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') dismissGuide();
    });
  }

  showNextPopup();
})();
