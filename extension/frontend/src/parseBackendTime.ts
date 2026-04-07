/**
 * Parse API datetime strings as UTC when no timezone is present.
 * Naive ISO strings from Python (e.g. datetime.now()) are wall-clock UTC on BlueOS;
 * `new Date("...T..")` without Z is interpreted as *local* time and shifts the calendar day
 * when we then format with timeZone: 'UTC'.
 */
export function parseBackendDateTime(raw: string): Date {
  let s = raw.trim()
  if (!s) return new Date(NaN)

  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    return new Date(`${s}T00:00:00Z`)
  }

  s = s.replace(/^(\d{4}-\d{2}-\d{2}) (\d)/, '$1T$2')

  if (s.includes('T')) {
    const fromT = s.slice(s.indexOf('T'))
    const hasTz = /Z$/i.test(s) || /[+-]\d{2}/.test(fromT)
    if (!hasTz) s = `${s}Z`
  }

  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? new Date(NaN) : d
}
