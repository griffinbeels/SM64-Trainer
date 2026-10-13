// Download details are rendered at build time and work without JavaScript.
// Deep links reveal the corresponding native, keyboard-accessible FAQ answer.
(() => {
  const revealAnswer = () => {
    if (!location.hash) return;
    const answer = document.getElementById(location.hash.slice(1));
    if (answer?.tagName === 'DETAILS') answer.open = true;
  };
  window.addEventListener('hashchange', revealAnswer);
  revealAnswer();
})();
