import {h} from 'preact';
import {useEffect, useLayoutEffect, useRef, useState} from 'preact/hooks';
import htm from 'htm';
import {pollJob, send} from '../api.js';
import {beginCommunitySignIn} from '../community.js';
import {Modal} from './modal.js';
import {CommunityProfile} from './communityprofile.js';
import {CommunitySettings} from './communitysettings.js';
import {ProgressPrivacy} from './progressprivacy.js';
import {ImportOutcome, useImportFlow} from './importflow.js';
import {ProgressLine} from './states.js';

const html = htm.bind(h);

// His decisions (round 41): Twitch or Discord only, and signing in is the
// first step: a configured build offers no way to practice without an
// account. An unconfigured build (source, offline beta) keeps the escape.
function ProviderChoices({enabled, busy, start, close}) {
  return html`<div class="community-join">
    <p>${enabled ? "Sign in to start practicing, race other players and share your ghosts."
                 : "Sign in to race other players and share your ghosts."}</p>
    ${!enabled && html`<p class="muted">Community ghosts are not configured in this build.</p>`}
    <div class="community-providers">
      <button class="community-provider community-discord" disabled=${busy || !enabled} onClick=${() => start('discord')}>
        <img src="/ui/assets/community/discord-clyde-white.svg" alt="" />Continue with Discord
      </button>
      <button class="community-provider community-twitch" disabled=${busy || !enabled} onClick=${() => start('twitch')}>
        <img src="/ui/assets/community/twitch-glitch-white.svg" alt="" />Continue with Twitch
      </button>
    </div>
    ${!enabled && html`<div class="community-center"><button disabled=${busy} onClick=${close}>Practice offline</button></div>`}
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
  // A sign-in started from Settings also finishes at practice/setup. Close
  // before the pending screen can turn into editable account settings.
  useLayoutEffect(() => {
    if (pending && community.account.authenticated && community.account.status === 'active') onClose();
  }, [pending, community.account.authenticated, community.account.status]);
  useEffect(() => {
    if (community.account.authenticated && community.account.status !== 'signed_out') setPending(null);
  }, [community.account.authenticated, community.account.status]);
  return {busy, error, pending, run, cancel,
    start: provider => run(async () => setPending(await beginCommunitySignIn(provider))),
    close: () => { cancel(); onClose(); }};
}

export function CommunityAccountModal({community, onClose, onPracticeRefresh, onOpenPlayer}) {
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
  // Signing in is the first step (round 41): while this build has no account
  // yet, or the account still needs a name or consent, the screen cannot be
  // dismissed. A forced rename keeps its own sign-out escape below.
  const rename = account.status === 'rename_required';
  const forced = account.enabled && (community.required
    || ['rename_required', 'new', 'consent_required'].includes(account.status));
  const save = (profile, importTimes) => actions.run(async () => {
    const sheetRunner = profile.sheet_runner;
    const importingTimes = importTimes && sheetRunner;
    if (importingTimes) {
      runner.current = sheetRunner; setImporting(true); community.setOpen(true);
    }
    try {
      await send('PUT', rename ? '/api/community/account/name' : '/api/community/account', rename ? {name: profile.name} : profile);
      await refresh();
      if (importingTimes) flow.run();
      else onClose();
    } catch (error) {
      if (importingTimes) { setImporting(false); community.setOpen(false); }
      throw error;
    }
  });
  const title = rename ? 'Choose a new name' : importing ? 'Import your best times' :
    !account.authenticated ? (account.enabled ? 'Sign in' : 'Community ghosts')
    : account.status === 'new' ? 'Choose your name' : 'Community account';
  return html`<${Modal} title=${title} onClose=${forced || actions.busy || flow.phase === 'working' ? undefined : actions.close}>
    <${AccountContent} account=${account} refresh=${refresh} actions=${actions} importing=${importing} flow=${flow} save=${save} onClose=${onClose} onOpenPlayer=${onOpenPlayer} />
    ${actions.error && html`<p class="community-error" role="alert">${actions.error}</p>`}
    ${rename && html`<div class="community-center"><button disabled=${actions.busy} onClick=${() => actions.run(async () => {
      await send('POST', '/api/community/auth/sign-out'); await refresh(); onClose();
    })}>Sign out</button></div>`}
  <//>`;
}

function AccountContent({account, refresh, actions, importing, flow, save, onClose, onOpenPlayer}) {
  if (importing) return html`${flow.progress && html`<${ProgressLine} progress=${flow.progress.progress} message=${flow.progress.message} running=${flow.phase === 'working'} />`}<${ImportOutcome} flow=${flow} />
      <div class="community-actions"><button disabled=${actions.busy || flow.phase === 'working'} onClick=${onClose}>Start practicing</button></div>`;
  if (actions.pending) return html`<p>Finish signing in in your browser.</p>
        ${!actions.pending.popup && html`<p><a href=${actions.pending.url} target="_blank" rel="noopener noreferrer">Open sign in</a></p>`}
        <button onClick=${actions.cancel}>Cancel</button>`;
  if (!account.authenticated) return html`<${ProviderChoices} enabled=${account.enabled} busy=${actions.busy} start=${actions.start} close=${actions.close} />`;
  if (account.status === 'signed_out') return html`<p role="status">Loading your account…</p>
    ${account.error && html`<p>Your account could not be loaded. Please try again.</p>`}
      <button disabled=${actions.busy} onClick=${() => actions.run(async () => {
        await send('POST', '/api/community/account/refresh'); await refresh();
      })}>Retry</button>
      <button disabled=${actions.busy} onClick=${() => actions.run(async () => {
        await send('POST', '/api/community/auth/sign-out'); await refresh(); onClose();
      })}>Sign out</button>`;
  if (['rename_required', 'new', 'consent_required'].includes(account.status)) return html`<${CommunityProfile} account=${account} busy=${actions.busy} onSave=${save} />
    ${account.status !== 'new' && account.uid ? html`<${ProgressPrivacy} account=${account} />` : null}`;
  if (account.status === 'active') return html`<${CommunitySettings} account=${account} busy=${actions.busy} run=${actions.run} refresh=${refresh} onClose=${onClose} onOpenPlayer=${onOpenPlayer} />`;
  return html`<${LapsedAccount} account=${account} actions=${actions} refresh=${refresh} onClose=${onClose} />`;
}

function LapsedAccount({account, actions, refresh, onClose}) {
  return html`<p>${account.status === 'suspended' ? 'Community access is paused for this account.'
    : account.status === 'deleted' ? 'This community account has been deleted.'
    : 'This beta account needs an invitation. Ask the person who sent you the build.'}</p>
        ${account.uid && account.name ? html`<${ProgressPrivacy} account=${account} />` : null}
        <button onClick=${() => actions.run(async () => { await send('POST', '/api/community/auth/sign-out'); await refresh(); onClose(); })}>Sign out</button>`;
}
