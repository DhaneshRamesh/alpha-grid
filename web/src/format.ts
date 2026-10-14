export function price(value?: string | null): string {
  if (value == null) return 'N/A'
  const number = BigInt(value)
  const sign = number < 0n ? '-' : ''
  const absolute = number < 0n ? -number : number
  const whole = absolute / 1_000_000_000n
  const cents = (absolute % 1_000_000_000n) / 10_000_000n
  return `${sign}₹${whole.toLocaleString('en-IN')}.${cents.toString().padStart(2, '0')}`
}
export function decimalPrice(value?: string | null): number | null {
  if (value == null) return null
  return Number(BigInt(value)) / 1_000_000_000
}
export function amount(value?: string | number | null): string {
  if (value == null) return 'N/A'
  return BigInt(value).toLocaleString('en-IN')
}
export function percent(value?: number | null): string {
  return value == null || !Number.isFinite(value) ? 'N/A' : `${(value * 100).toFixed(2)}%`
}
export function ratio(value?: number | null): string {
  return value == null || !Number.isFinite(value) ? 'N/A' : value.toFixed(4)
}
export function latency(value?: string | null): string {
  return value == null ? 'N/A' : `${(Number(BigInt(value)) / 1_000_000).toFixed(2)} ms`
}
export function time(value?: string | null): string {
  if (value == null) return 'N/A'
  return new Date(Number(BigInt(value) / 1_000_000n)).toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' }) + ' IST'
}
export function dateTime(value?: string | null): string {
  if (value == null) return 'N/A'
  return new Date(Number(BigInt(value) / 1_000_000n)).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false, dateStyle: 'medium', timeStyle: 'medium' }) + ' IST'
}
export function delta(current?: string | null, previous?: string | null): string {
  return current == null || previous == null ? 'N/A' : price((BigInt(current) - BigInt(previous)).toString())
}
