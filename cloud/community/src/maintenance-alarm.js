import {DurableObject} from 'cloudflare:workers';
import {MaintenanceClock} from './maintenance.js';

// Internal binding only. No public route forwards fetch or arbitrary method names.
export class MaintenanceAlarm extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.clock = new MaintenanceClock(ctx.storage, env); }
  async arm() { return this.clock.arm(); }
  async alarm() { return this.clock.alarm(); }
  async status() { return this.clock.status(); }
}
