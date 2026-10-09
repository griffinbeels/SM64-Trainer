// The account page reads cached, token-free state from the local server.
import { useEffect, useState } from "preact/hooks";
import { getJSON, send } from "./api.js";

export function useCommunityAccount() {
  const [account, setAccount] = useState({enabled: false, authenticated: false, status: 'signed_out'});
  const [open, setOpen] = useState(false);
  const refresh = () => getJSON("/api/community/account").then(setAccount);
  useEffect(() => { refresh().catch(() => setAccount({ enabled: false })); }, []);
  useEffect(() => {
    if (!account.enabled) return;
    const timer = setInterval(() => refresh().catch(() => {}), 10000);
    return () => clearInterval(timer);
  }, [account.enabled]);
  useEffect(() => {
    if (account.status === "rename_required" || account.status === "consent_required") setOpen(true);
    if (account.status === 'consent_required') {
      setAccount(current => ({...current, policies_loading: true}));
      send('POST', '/api/community/account/refresh').then(setAccount)
        .catch(() => setAccount(current => ({...current, policies_loading: false})));
    }
  }, [account?.status]);
  const refreshRemote = () => send('POST', '/api/community/account/refresh').then(setAccount);
  return { account, open, setOpen, refresh, refreshRemote, setAccount };
}

export async function beginCommunitySignIn(provider) {
  // Open on the gesture so browser popup blocking does not add another step.
  const popup = window.open("about:blank", "_blank");
  if (popup) popup.opener = null;
  try {
    const attempt = await send("POST", "/api/community/auth/start", { provider });
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
