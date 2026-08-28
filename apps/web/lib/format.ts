/**
 * Small formatting helpers shared across pages. Keep these dependency-free –
 * heavier date/locale code can come in if/when we add Intl.DateTimeFormat
 * customisation in Settings → General → Locale.
 */

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return new Intl.NumberFormat().format(n);
}

export function formatDateRange(min: string | null, max: string | null): string {
  if (!min || !max) return "–";
  const a = new Date(min);
  const b = new Date(max);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return "–";
  const fmt = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  if (fmt.format(a) === fmt.format(b)) return fmt.format(a);
  return `${fmt.format(a)} → ${fmt.format(b)}`;
}

/**
 * Naive UTC wall-clock string (`YYYY-MM-DDTHH:MM:SS`, no offset) for an epoch.
 *
 * Event-log data is stored as naive-UTC timestamps in Parquet, so filter/edit
 * literals sent to the backend (compared against DuckDB TIMESTAMP columns)
 * must be the instant's *UTC* components without an offset suffix.
 */
export function toNaiveUtcString(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
  );
}

/**
 * Local wall-clock string (`YYYY-MM-DDTHH:MM:SS`) for an epoch – the value
 * format `<input type="datetime-local">` expects, matching what
 * `toLocaleString()` shows the user elsewhere.
 */
export function toLocalInputString(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

const RELATIVE_THRESHOLDS: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60 * 60, "minute"],
  [60 * 60 * 24, "hour"],
  [60 * 60 * 24 * 30, "day"],
  [60 * 60 * 24 * 365, "month"],
  [Number.POSITIVE_INFINITY, "year"],
];

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "–";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "–";
  const deltaSec = Math.round((ts - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const abs = Math.abs(deltaSec);

  for (let i = 0; i < RELATIVE_THRESHOLDS.length; i++) {
    const [limit, unit] = RELATIVE_THRESHOLDS[i];
    if (abs < limit) {
      const divisor = i === 0 ? 1 : RELATIVE_THRESHOLDS[i - 1][0];
      return rtf.format(Math.round(deltaSec / divisor), unit);
    }
  }
  return iso;
}

const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;
const SECONDS_PER_DAY = 86_400;
const SECONDS_PER_MONTH = SECONDS_PER_DAY * 30;
const SECONDS_PER_YEAR = SECONDS_PER_DAY * 365;

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  if (seconds === 0) return "0 s";

  const sign = seconds < 0 ? "-" : "";
  const abs = Math.abs(seconds);

  if (abs < 1e-3) {
    return `${sign}~${Math.round(abs * 1_000_000)} µs`;
  }
  if (abs < 1) {
    return `${sign}~${Math.round(abs * 1_000)} ms`;
  }
  if (abs < SECONDS_PER_MINUTE) {
    return `${sign}~${Math.round(abs)} s`;
  }
  if (abs < SECONDS_PER_HOUR) {
    const m = Math.floor(abs / SECONDS_PER_MINUTE);
    const s = Math.round(abs % SECONDS_PER_MINUTE);
    return s ? `${sign}~${m} min ${s} s` : `${sign}~${m} min`;
  }
  if (abs < SECONDS_PER_DAY) {
    const h = Math.floor(abs / SECONDS_PER_HOUR);
    const m = Math.round((abs % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE);
    return m ? `${sign}~${h} h ${m} min` : `${sign}~${h} h`;
  }
  if (abs < SECONDS_PER_MONTH) {
    const d = Math.floor(abs / SECONDS_PER_DAY);
    const h = Math.round((abs % SECONDS_PER_DAY) / SECONDS_PER_HOUR);
    return h ? `${sign}~${d} d ${h} h` : `${sign}~${d} d`;
  }
  if (abs < SECONDS_PER_YEAR) {
    const mo = Math.floor(abs / SECONDS_PER_MONTH);
    const d = Math.round((abs % SECONDS_PER_MONTH) / SECONDS_PER_DAY);
    return d ? `${sign}~${mo} mo ${d} d` : `${sign}~${mo} mo`;
  }
  const y = Math.floor(abs / SECONDS_PER_YEAR);
  const mo = Math.round((abs % SECONDS_PER_YEAR) / SECONDS_PER_MONTH);
  return mo ? `${sign}~${y} y ${mo} mo` : `${sign}~${y} y`;
}
