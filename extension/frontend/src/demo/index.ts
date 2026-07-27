/**
 * Demo mode entry point.
 *
 * Loaded only via dynamic import from `main.ts` when `VITE_DEMO_MODE` is
 * `true`. Vite replaces that flag with a literal at build time, so the
 * branch and this whole module tree are dropped from the production
 * bundle that ships to the vehicle.
 */
import { installApiMock } from './api'
import { installAttitudeMock } from './attitude'

/**
 * Fixed marker so a reviewer never mistakes this for a live vehicle.
 * Injected into the DOM rather than added to `App.vue`, which keeps the
 * demo diff off every shared component and makes rebasing onto `main`
 * effectively conflict-free.
 */
function installBanner(): void {
  const badge = document.createElement('div')
  badge.setAttribute('role', 'status')
  badge.textContent = 'DEMO — simulated data, no vehicle connected'

  Object.assign(badge.style, {
    position: 'fixed',
    right: '12px',
    bottom: '12px',
    zIndex: '2147483647',
    padding: '6px 12px',
    borderRadius: '999px',
    background: 'rgba(180, 83, 9, 0.92)',
    color: '#fff',
    font: "500 12px/1.2 system-ui, -apple-system, 'Segoe UI', sans-serif",
    letterSpacing: '0.02em',
    boxShadow: '0 2px 10px rgba(0, 0, 0, 0.35)',
    cursor: 'pointer',
    opacity: '0.55',
    transition: 'opacity 140ms ease',
    userSelect: 'none',
  } satisfies Partial<CSSStyleDeclaration>)

  badge.addEventListener('mouseenter', () => { badge.style.opacity = '1' })
  badge.addEventListener('mouseleave', () => { badge.style.opacity = '0.55' })
  badge.title = 'Click to hide until reload'
  badge.addEventListener('click', () => badge.remove())

  document.body.appendChild(badge)
}

export function installDemoMode(): void {
  installApiMock()
  installAttitudeMock()

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installBanner, { once: true })
  } else {
    installBanner()
  }

  console.info('[demo] DORIS UI running on simulated data — no vehicle connected.')
}
