/* The six reads the Progress tab draws from, fetched in the live wave, and the one event that
 * says any of them changed. */

import { pendingNotice } from "../../kit/dashkit";
import { createListeners } from "../../app/listeners";
import { loadOne } from "../../app/load";
import { registerFetch } from "../../app/registry";
import { onSetting, setSetting, settingOn, spoilerNotice } from "../../app/settings";
import { offer } from "../../kit/toast";

import type { ApiError, ApiUrl } from "../../api/client";
import type { Fetcher } from "../../app/registry";
import type {
  HardDrivesResponse,
  MamResponse,
  MilestonesResponse,
  PhaseResponse,
  ShardsResponse,
  SloopsResponse,
} from "../../api/shapes";

export interface ProgressFeed<T> {
  data: T | null;
  failed: boolean;
  path: ApiUrl;
  label: string;
}

const progress = createListeners();

export function changed(): void {
  progress.emit();
}

export function onProgress(listener: () => void): void {
  progress.on(listener);
}

function feed<T>(path: ApiUrl, label: string): ProgressFeed<T> {
  return { data: null, failed: false, path: path, label: label };
}

function fetcher<T extends ApiError>(held: ProgressFeed<T>, rank: number): Fetcher<T> {
  return {
    wave: "live",
    rank: rank,
    path: held.path,
    label: held.label,
    clears: [],
    refilters: false,
    draw: function (data) {
      held.data = data;
      held.failed = false;
      changed();
    },
    failed: function () {
      held.data = null;
      held.failed = true;
      changed();
    },
  };
}

export const milestones = feed<MilestonesResponse>("/api/progress/milestones", "milestones");
export const mam = feed<MamResponse>("/api/progress/mam", "MAM research");
export const phase = feed<PhaseResponse>("/api/progress/phase", "space elevator");
export const drives = feed<HardDrivesResponse>("/api/progress/harddrives", "hard drives");
export const shards = feed<ShardsResponse>("/api/progress/shards", "power shards");
export const sloops = feed<SloopsResponse>("/api/progress/sloops", "somersloops");

registerFetch(fetcher(milestones, 60));
registerFetch(fetcher(mam, 61));
registerFetch(fetcher(phase, 62));
registerFetch(fetcher(drives, 63));
registerFetch(fetcher(shards, 64));
registerFetch(fetcher(sloops, 65));

onSetting(changed);

if (spoilerNotice()) {
  offer("upcoming milestones, research and locked recipes are now hidden until the save reaches them", "show them", function () {
    setSetting("spoilers", true);
  });
}

export function waiting<T>(body: HTMLElement, held: ProgressFeed<T>): void {
  pendingNotice(body, held.label, null, held.failed, function () {
    loadOne(held.path);
  });
}

export function visible<T extends { spoiler: boolean }>(rows: T[]): T[] {
  if (settingOn("spoilers")) return rows;
  return rows.filter(function (row) {
    return !row.spoiler;
  });
}
