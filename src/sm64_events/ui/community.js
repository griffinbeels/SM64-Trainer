// The account page reads cached, token-free state from the local server.
import { useEffect, useState } from "preact/hooks";
import { getJSON, send } from "./api.js";

export function useCommunityAccount() {
  const [account, setAccount] = useState({enabled: false, authenticated: false, status: 'signed_out'});
  const [loading, setLoading] = useState(true);
  // Explicit open state keeps Settings or a requested Sheet import visible.
  // Automatic sign-in/profile steps
  // follow accountRequired and disappear as soon as an active account arrives.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("community-account-changed"));
  }, [account.uid, account.status, account.authenticated]);
  const refresh = () => getJSON("/api/community/account").then(value => {
    setAccount(value); setLoading(false); return value;
  });
  useEffect(() => { refresh().catch(() => {
    setAccount({ enabled: false }); setLoading(false);
  }); }, []);
  useEffect(() => {
    if (!account.enabled) return;
    const pendingProfile = account.authenticated && account.status === "signed_out";
    const timer = setInterval(() => refresh().catch(() => {}), pendingProfile ? 1000 : 10000);
    return () => clearInterval(timer);
  }, [account.enabled, account.authenticated, account.status]);
  useEffect(() => {
    if (account.status === 'consent_required') {
      setAccount(current => ({...current, policies_loading: true}));
      send('POST', '/api/community/account/refresh').then(setAccount)
        .catch(() => setAccount(current => ({...current, policies_loading: false})));
    }
  }, [account?.status]);
  const refreshRemote = () => send('POST', '/api/community/account/refresh').then(setAccount);
  // Signing in is the first step (round 41): a configured build with no
  // account on this profile, or an account still choosing a name or
  // consenting, keeps the sign-in screen open. A stored account that is
  // offline right now is not asked again; practice continues.
  const required = accountRequired(account);
  // While required, the screen is open whatever a close handler did; a
  // sign-out from a forced rename lands back on the providers at once.
  return { account, loading, open: open || required, setOpen, refresh, refreshRemote, setAccount, required };
}

export function accountRequired(account) {
  if (!account?.enabled) return false;
  // Token exchange can finish before the remote profile arrives. That is
  // still sign-in, rather than an active account ready for emulator setup.
  if (account.authenticated) return ["signed_out", "new", "rename_required", "consent_required"].includes(account.status);
  return !account.stored;
}

export function insideDesktopShell() {
  // pywebview injects this object into every page it hosts; a browser tab has none.
  return typeof window !== "undefined" && Boolean(window.pywebview);
}

export async function beginCommunitySignIn(provider) {
  // The desktop shell hands every new window to the OS as a URL, so a blank
  // tab opened here became Windows asking for an app to open an 'about' link
  // (round 39). Inside the shell the server opens the default browser itself.
  // A browser tab still opens on the gesture so popup blocking cannot add a step.
  const shell = insideDesktopShell();
  const popup = shell ? null : window.open("about:blank", "_blank");
  if (popup) popup.opener = null;
  try {
    const attempt = await send("POST", "/api/community/auth/start",
                               shell ? { provider, open_browser: true } : { provider });
    if (popup) popup.location.replace(attempt.url);
    return { ...attempt, popup };
  } catch (error) {
    popup?.close();
    throw error;
  }
}

export function downloadAccountData(value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = "sm64-community-account.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
