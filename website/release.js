// Download details are rendered at build time and work without JavaScript.
// Deep links reveal the corresponding native, keyboard-accessible FAQ answer.
(() => {
  const uploadTime = document.querySelector('time[data-release-upload]');
  if (uploadTime) {
    const date = new Date(uploadTime.dateTime);
    if (Number.isFinite(date.getTime())) {
      uploadTime.title = uploadTime.textContent;
      uploadTime.textContent = new Intl.DateTimeFormat(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
      }).format(date);
    }
  }
  const revealAnswer = () => {
    if (!location.hash) return;
    const answer = document.getElementById(location.hash.slice(1));
    if (answer?.tagName === 'DETAILS') answer.open = true;
  };
  window.addEventListener('hashchange', revealAnswer);
  revealAnswer();
})();
