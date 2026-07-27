/**
 * Fake MAVLink attitude WebSocket for demo mode.
 *
 * `useAttitudeWs.ts` opens `ws://<host>:6040/ws/mavlink?filter=ATTITUDE`
 * against mavlink2rest, which does not exist on a static host. Without a
 * stub it would retry every 2 s forever and the 3D vehicle would never
 * move, so this feeds it a slow synthetic roll/pitch/yaw instead.
 *
 * Only mavlink URLs are intercepted; anything else gets the real
 * WebSocket.
 */

const ATTITUDE_HZ = 20
const RealWebSocket = globalThis.WebSocket

type Listener = ((ev: MessageEvent) => void) | null

class DemoAttitudeSocket implements Partial<WebSocket> {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readonly CONNECTING = 0 as const
  readonly OPEN = 1 as const
  readonly CLOSING = 2 as const
  readonly CLOSED = 3 as const

  readyState: number = DemoAttitudeSocket.CONNECTING
  url: string

  onopen: ((ev: Event) => void) | null = null
  onmessage: Listener = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null

  private timer: number | undefined
  private readonly start = Date.now()

  constructor(url: string) {
    this.url = url

    // Defer so the caller can attach handlers before anything fires.
    setTimeout(() => {
      this.readyState = DemoAttitudeSocket.OPEN
      this.onopen?.(new Event('open'))
      this.timer = window.setInterval(() => this.emit(), 1000 / ATTITUDE_HZ)
    }, 120)
  }

  private emit(): void {
    if (this.readyState !== DemoAttitudeSocket.OPEN) return

    const t = (Date.now() - this.start) / 1000

    // Gentle mixed-period motion so the attitude view reads as a vehicle
    // sitting in moving water rather than a looping animation.
    const roll = 0.16 * Math.sin(t * 0.42) + 0.05 * Math.sin(t * 1.31)
    const pitch = 0.10 * Math.sin(t * 0.29 + 1.2) + 0.03 * Math.sin(t * 0.97)
    const yaw = 2.42 + 0.22 * Math.sin(t * 0.11)

    this.onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({
          message: {
            type: 'ATTITUDE',
            time_boot_ms: Math.round((Date.now() - this.start) + 9_900_000),
            roll,
            pitch,
            yaw,
            rollspeed: 0.16 * 0.42 * Math.cos(t * 0.42),
            pitchspeed: 0.10 * 0.29 * Math.cos(t * 0.29 + 1.2),
            yawspeed: 0.22 * 0.11 * Math.cos(t * 0.11),
          },
        }),
      }),
    )
  }

  close(): void {
    this.readyState = DemoAttitudeSocket.CLOSED
    if (this.timer !== undefined) {
      clearInterval(this.timer)
      this.timer = undefined
    }
    this.onclose?.(new CloseEvent('close'))
  }

  send(): void {
    /* the attitude stream is read-only */
  }

  addEventListener(): void {
    /* useAttitudeWs assigns handlers directly */
  }

  removeEventListener(): void {
    /* useAttitudeWs assigns handlers directly */
  }
}

export function installAttitudeMock(): void {
  const Patched = function (this: unknown, url: string | URL, protocols?: string | string[]) {
    const href = typeof url === 'string' ? url : url.href
    if (href.includes('/ws/mavlink')) {
      return new DemoAttitudeSocket(href)
    }
    return new RealWebSocket(url, protocols)
  } as unknown as typeof WebSocket

  Object.defineProperties(Patched, {
    CONNECTING: { value: 0 },
    OPEN: { value: 1 },
    CLOSING: { value: 2 },
    CLOSED: { value: 3 },
  })

  globalThis.WebSocket = Patched
}
