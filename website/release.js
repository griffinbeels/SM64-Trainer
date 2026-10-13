/* Live prototype feed. Production will publish verified static release metadata. */
(() => {
  const repository = 'griffinbeels/SM64-Trainer';
  const releasePage = `https://github.com/${repository}/releases/latest`;
  const metadata = document.querySelector('#release-meta');
  const links = [...document.querySelectorAll('.download-link')];
  const fallback = () => {
    links.forEach(link => {
      link.href = releasePage;
      link.querySelector('.download-label').textContent = 'View Windows download';
    });
    metadata.textContent = 'Latest version, download & checksums on GitHub.';
  };
  const validAsset = (asset, tag, name) => {
    if (!asset || asset.state !== 'uploaded' || !Number.isSafeInteger(asset.size) || asset.size <= 0) return false;
    try {
      const url = new URL(asset.browser_download_url);
      return url.protocol === 'https:' && url.hostname === 'github.com' &&
        decodeURIComponent(url.pathname) === `/${repository}/releases/download/${tag}/${name}` &&
        !url.search && !url.hash;
    } catch { return false; }
  };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 6500);
  fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json' }, signal: controller.signal,
    credentials: 'omit', cache: 'no-store',
  }).then(async response => {
    if (!response.ok) throw new Error('Release feed unavailable');
    const release = await response.json();
    const tag = release.tag_name;
    if (release.draft || release.prerelease || typeof tag !== 'string' || !/^v\d+\.\d+\.\d+$/.test(tag) ||
        String(release.body || '').includes('<!-- sm64-withdrawn -->') || !Array.isArray(release.assets)) throw new Error('Not a production release');
    const installer = release.assets.find(asset => asset.name === 'SM64Trainer.exe');
    const checksum = release.assets.find(asset => asset.name === 'SM64Trainer.exe.sha256');
    if (!validAsset(installer, tag, 'SM64Trainer.exe') || !validAsset(checksum, tag, 'SM64Trainer.exe.sha256')) throw new Error('Installer unavailable');
    const uploadDate = new Date(installer.created_at);
    if (!Number.isFinite(uploadDate.getTime())) throw new Error('Upload date unavailable');
    links.forEach(link => {
      link.href = installer.browser_download_url;
      link.querySelector('.download-label').textContent = 'Download for Windows';
    });
    const date = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(uploadDate);
    metadata.replaceChildren(document.createTextNode(`${tag} · ${(installer.size / 1_000_000).toFixed(1)} MB · Uploaded ${date} `));
    const notes = document.createElement('a');
    notes.textContent = 'Release notes ↗';
    notes.href = `https://github.com/${repository}/releases/tag/${encodeURIComponent(tag)}`;
    notes.target = '_blank'; notes.rel = 'noopener noreferrer';
    metadata.append(notes);
  }).catch(fallback).finally(() => clearTimeout(timeout));
  // Native details remain usable without JS. Deep links also reveal their answer.
  const revealAnswer = () => {
    if (!location.hash) return;
    const answer = document.getElementById(location.hash.slice(1));
    if (answer?.tagName === 'DETAILS') answer.open = true;
  };
  window.addEventListener('hashchange', revealAnswer);
  revealAnswer();
})();
