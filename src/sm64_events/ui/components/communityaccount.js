import {h} from 'preact';
import {useEffect, useRef, useState} from 'preact/hooks';
import htm from 'htm';
import {pollJob, send} from '../api.js';
import {beginCommunitySignIn} from '../community.js';
import {Modal} from './modal.js';
import {CommunityProfile} from './communityprofile.js';
import {CommunitySettings} from './communitysettings.js';
import {ImportOutcome, useImportFlow} from './importflow.js';
import {ProgressLine} from './states.js';

const html = htm.bind(h);

function ProviderChoices({enabled, busy, start, close}) {
  return html`<div class="community-join">
    <p>Sign in to race other players and share your ghosts.</p>
    ${!enabled && html`<p class="muted">Community ghosts are not configured in this build.</p>`}
    <div class="community-providers">
      <button class="community-provider community-discord" disabled=${busy || !enabled} onClick=${() => start('discord')}>
        <img src="/ui/assets/community/discord-clyde-white.svg" alt="" />Continue with Discord
      </button>
      <button class="community-provider community-google" disabled=${busy || !enabled} onClick=${() => start('google')}>
        <img src="/ui/assets/community/google-g.png" alt="" />Continue with Google
      </button>
    </div>
    <div class="community-center"><button class="link" disabled=${busy || !enabled} onClick=${() => start('email')}>Continue with email</button></div>
    <div class="community-center"><button disabled=${busy} onClick=${close}>Practice offline</button></div>
  </div>`;
}

function useSignInPoll(pending, refresh, cancel) {
  useEffect(() => {
    if (!pending) return;
    let stopped = false, working = false;
    const deadline = Date.now() + 300000;
    const timer = setInterval(() => {
      if (stopped || working) return;
      if (Date.now() > deadline || pending.popup?.closed) { cancel(); return; }
      working = true;
      refresh().catch(() => {}).finally(() => { working = false; });
    }, 1000);
    return () => { stopped = true; clearInterval(timer); };
  }, [pending]);
}

function useAccountActions(community, onClose) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [pending, setPending] = useState(null);
  const run = async work => {
    setBusy(true); setError('');
    try { return await work(); }
    catch (e) { setError(e.message || 'Please try again.'); }
    finally { setBusy(false); }
  };
  const cancel = () => {
    if (pending) send('POST', '/api/community/auth/cancel', {attempt_id: pending.attempt_id}).catch(() => {});
    pending?.popup?.close(); setPending(null);
  };
  useSignInPoll(pending, community.refresh, cancel);
  useEffect(() => {
    if (community.account.authenticated && community.account.status !== 'signed_out') setPending(null);
  }, [community.account.authenticated, community.account.status]);
  return {busy, error, pending, run, cancel,
    start: provider => run(async () => setPending(await beginCommunitySignIn(provider))),
    close: () => { cancel(); onClose(); }};
}

export function CommunityAccountModal({community, onClose, onPracticeRefresh}) {
  const {account, refresh} = community;
  const actions = useAccountActions(community, onClose);
  const runner = useRef(null), [importing, setImporting] = useState(false);
  const flow = useImportFlow({
    source: `sheet:${runner.current}`, post: async report => {
      const job = await send('POST', '/api/import/sheet/job', {runner: runner.current});
      return pollJob(`/api/import/sheet/job/${job.job_id}`, report);
    },
    onDone: onPracticeRefresh,
  });
  const forced = account.status === 'rename_required';
  const save = (profile, importTimes) => actions.run(async () => {
    const sheetRunner = profile.sheet_runner;
    await send('PUT', forced ? '/api/community/account/name' : '/api/community/account', forced ? {name: profile.name} : profile);
    await refresh();
    if (importTimes && sheetRunner) {
      runner.current = sheetRunner; setImporting(true); flow.run();
    } else onClose();
  });
  const title = forced ? 'Choose a new name' : importing ? 'Import your best times' :
    !account.authenticated ? 'Community ghosts' : account.status === 'new' ? 'Choose your name' : 'Community account';
  return html`<${Modal} title=${title} onClose=${forced || actions.busy || flow.phase === 'working' ? undefined : actions.close}>
    <${AccountContent} account=${account} refresh=${refresh} actions=${actions} importing=${importing} flow=${flow} save=${save} onClose=${onClose} />
    ${actions.error && html`<p class="community-error" role="alert">${actions.error}</p>`}
    ${forced && html`<div class="community-center"><button disabled=${actions.busy} onClick=${() => actions.run(async () => {
      await send('POST', '/api/community/auth/sign-out'); await refresh(); onClose();
    })}>Practice offline</button></div>`}
  <//>`;
}

function AccountContent({account, refresh, actions, importing, flow, save, onClose}) {
  if (importing) return html`${flow.progress && html`<${ProgressLine} progress=${flow.progress.progress} message=${flow.progress.message} running=${flow.phase === 'working'} />`}<${ImportOutcome} flow=${flow} />
      <div class="community-actions"><button disabled=${flow.phase === 'working'} onClick=${onClose}>Start practicing</button></div>`;
  if (actions.pending) return html`<p>Finish signing in in your browser.</p>
        ${!actions.pending.popup && html`<p><a href=${actions.pending.url} target="_blank" rel="noopener noreferrer">Open sign in</a></p>`}
        <button onClick=${actions.cancel}>Cancel</button>`;
  if (!account.authenticated) return html`<${ProviderChoices} enabled=${account.enabled} busy=${actions.busy} start=${actions.start} close=${actions.close} />`;
  if (['rename_required', 'new', 'consent_required'].includes(account.status)) return html`<${CommunityProfile} account=${account} busy=${actions.busy} onSave=${save} />`;
  if (account.status === 'active') return html`<${CommunitySettings} account=${account} busy=${actions.busy} run=${actions.run} refresh=${refresh} onClose=${onClose} />`;
  return html`<p>This beta account needs an invitation. Ask the person who sent you the build.</p>
        <button onClick=${() => actions.run(async () => { await send('POST', '/api/community/auth/sign-out'); await refresh(); onClose(); })}>Sign out</button>`;
}
