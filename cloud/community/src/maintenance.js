import {deleteIdentities} from './identity-deletion.js';
import {regrade} from './regrade.js';
import {reconcile} from './reconcile.js';

export const MAINTENANCE_INTERVAL_MS = 15 * 60 * 1000;

// Separate stages keep a provider outage from delaying unrelated storage cleanup.
// Persist only fixed outcome labels: upstream errors may contain credentials.
export async function runMaintenance(env, stages = {identity:deleteIdentities, catalog:regrade, storage:reconcile}) {
  const result = {};
  for (const [name, stage] of Object.entries(stages)) {
    try { const outcome=await stage(env); result[name] = outcome?.retried||outcome?.configuration_missing?'retry':'ok'; }
    catch { result[name] = 'retry'; }
  }
  return result;
}

export function armMaintenance(env, ctx) {
  if (!env.MAINTENANCE) return;
  let operation;
  try { operation = Promise.resolve(env.MAINTENANCE.getByName('community-maintenance').arm()).catch(()=>{}); }
  catch { return; }
  // A transient scheduler failure must not interrupt a successful game request.
  ctx?.waitUntil?.(operation);
  return operation;
}

// The production Durable Object delegates to this same testable state machine.
export class MaintenanceClock {
  constructor(storage, env, run = runMaintenance) {
    this.storage = storage; this.env = env; this.run = run;
  }
  async arm() {
    const current = await this.storage.getAlarm();
    if (current === null) await this.storage.setAlarm(Date.now() + MAINTENANCE_INTERVAL_MS);
    return {armed:true};
  }
  async alarm() {
    // Set the next durable alarm before external IO. A timeout or long upstream
    // outage must not exhaust the platform's finite automatic retry allowance.
    await this.storage.setAlarm(Date.now() + MAINTENANCE_INTERVAL_MS);
    const attempt = Date.now();
    await this.storage.put('last_attempt_ms', attempt);
    let result;
    try { result = await this.run(this.env); }
    catch { result = {maintenance:'retry'}; }
    await this.storage.put('last_result', {attempt_ms:attempt,finished_ms:Date.now(),stages:result});
  }
  async status() {
    return {next_alarm_ms:await this.storage.getAlarm(), last_attempt_ms:await this.storage.get('last_attempt_ms') ?? null,
      last_result:await this.storage.get('last_result') ?? null};
  }
}
