/**
 * Most recent Sunday on or before today, as a local-date "YYYY-MM-DD" string.
 * Weeks start Sunday, matching the backend's week_start_for().
 */
export function currentWeekStart(d: Date = new Date()): string {
  const local = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  local.setDate(local.getDate() - local.getDay()); // getDay(): Sun = 0
  const y = local.getFullYear();
  const m = String(local.getMonth() + 1).padStart(2, "0");
  const day = String(local.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
