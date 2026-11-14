import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ChevronDown, LogOut, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { Badge } from './components/ui/badge'
import { Separator } from './components/ui/separator'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from './components/ui/dropdown-menu'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './components/ui/sheet'

type Link = {label: string; href: string; current: boolean; icon?: string}
const readLink = (node: HTMLAnchorElement): Link => ({
  label: node.textContent?.trim() || node.getAttribute('aria-label') || '',
  href: node.getAttribute('href') || '#',
  current: node.getAttribute('aria-current') === 'page',
  icon: node.querySelector('use')?.getAttribute('href') || undefined,
})

/** Keep the existing server-authenticated POST form, including its CSRF token. */
function AccountMenu({host, links, form}: {host: HTMLElement; links: Link[]; form: HTMLFormElement | null}) {
  const login = host.dataset.variant === 'login'
  const label = host.dataset.label || (login ? '其他登录方式' : '我的账户')
  const subtitle = host.dataset.subtitle || ''
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button variant="ghost" className={`hu-account-trigger${login ? ' hu-login-trigger' : ''}`} aria-label={login ? label : `${label}，账户菜单`}>
        {!login && <span className="hu-avatar" aria-hidden="true">{label.slice(0, 1)}</span>}
        <span className="hu-account-copy"><span>{label}</span>{subtitle && <small>{subtitle}</small>}</span>
        <ChevronDown aria-hidden="true" className="hu-chevron" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent className={document.body.classList.contains('entry-page') ? 'hu-entry' : ''} align="end" sideOffset={8} collisionPadding={12}>
      <DropdownMenuLabel>{login ? '选择工作身份' : label}{subtitle && <small className="hu-menu-subtitle">{subtitle}</small>}</DropdownMenuLabel>
      <DropdownMenuSeparator />
      {links.map((link, index) => <DropdownMenuItem asChild key={`${link.href}-${index}`}><a href={link.href} aria-current={link.current ? 'page' : undefined}>{link.label}</a></DropdownMenuItem>)}
      {form && <><DropdownMenuSeparator /><DropdownMenuItem onSelect={() => form.requestSubmit()}><LogOut aria-hidden="true" />退出登录</DropdownMenuItem></>}
    </DropdownMenuContent>
  </DropdownMenu>
}

function NavigationSheet({sidebar}: {sidebar: HTMLElement}) {
  const [open, setOpen] = useState(false)
  const opener = useRef<HTMLElement | null>(null)
  const compact = useRef(window.matchMedia('(max-width: 1199px)'))
  const groups = [...sidebar.querySelectorAll('nav')].map(nav => ({
    title: nav.getAttribute('aria-label') || '导航',
    links: [...nav.querySelectorAll<HTMLAnchorElement>('a[href]')].map(readLink),
  })).filter(group => group.links.length)
  const brand = sidebar.querySelector<HTMLAnchorElement>('.brand')
  const active = groups.flatMap(group => group.links).find(link => link.current)
  useEffect(() => {
    const toggles = [...document.querySelectorAll<HTMLElement>('[data-nav-toggle]')]
    const previousAttributes = toggles.map(toggle => ({toggle, controls: toggle.getAttribute('aria-controls'), popup: toggle.getAttribute('aria-haspopup')}))
    const resetLegacyNavigation = () => {
      sidebar.classList.remove('open')
      sidebar.removeAttribute('role')
      sidebar.removeAttribute('aria-modal')
      sidebar.inert = compact.current.matches
      document.querySelector<HTMLElement>('.workspace')?.removeAttribute('inert')
      document.querySelector<HTMLElement>('.mobile-nav')?.removeAttribute('inert')
      const backdrop = document.querySelector<HTMLElement>('.nav-backdrop')
      if (backdrop) backdrop.hidden = true
      document.body.classList.remove('nav-open')
    }
    const handle = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-nav-toggle]') : null
      if (!target || !compact.current.matches) return
      event.preventDefault()
      event.stopImmediatePropagation()
      opener.current = target
      resetLegacyNavigation()
      setOpen(true)
    }
    const media = () => { resetLegacyNavigation(); setOpen(false) }
    document.addEventListener('click', handle, true)
    compact.current.addEventListener('change', media)
    window.addEventListener('pageshow', media)
    resetLegacyNavigation()
    sidebar.dataset.uiNavigationReady = 'true'
    toggles.forEach(toggle => {toggle.setAttribute('aria-haspopup', 'dialog'); toggle.setAttribute('aria-controls', 'hu-navigation-sheet')})
    return () => {
      document.removeEventListener('click', handle, true)
      compact.current.removeEventListener('change', media)
      window.removeEventListener('pageshow', media)
      delete sidebar.dataset.uiNavigationReady
      previousAttributes.forEach(({toggle, controls, popup}) => {
        if (controls === null) toggle.removeAttribute('aria-controls'); else toggle.setAttribute('aria-controls', controls)
        if (popup === null) toggle.removeAttribute('aria-haspopup'); else toggle.setAttribute('aria-haspopup', popup)
        toggle.setAttribute('aria-expanded', 'false')
      })
    }
  }, [sidebar])
  useEffect(() => {
    document.querySelectorAll('[data-nav-toggle]').forEach(toggle => toggle.setAttribute('aria-expanded', String(open)))
  }, [open])
  return <Sheet open={open} onOpenChange={setOpen}>
    <SheetContent id="hu-navigation-sheet" side="left" showCloseButton={false} onCloseAutoFocus={event => {event.preventDefault(); opener.current?.focus()}}>
      <SheetHeader>
        <div className="hu-sheet-heading"><SheetTitle>全部导航</SheetTitle><Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="关闭导航"><X aria-hidden="true" /></Button></div>
        <SheetDescription>选择要前往的页面</SheetDescription>
        {brand && <a className="hu-sidebar-brand" href={brand.href} aria-label={brand.getAttribute('aria-label') || '活动主页'} dangerouslySetInnerHTML={{__html: brand.innerHTML}} />}
        {active && <Badge variant="outline">当前：{active.label}</Badge>}
      </SheetHeader>
      <Separator />
      <div className="hu-sheet-scroll">
        {groups.map((group, index) => <nav key={index} className="hu-navigation-group" aria-label={group.title}>
          <p className="hu-navigation-label">{group.title}</p>
          {group.links.map((link, linkIndex) => <Button asChild variant={link.current ? 'secondary' : 'ghost'} className="hu-navigation-link" key={`${link.href}-${linkIndex}`}><a href={link.href} aria-current={link.current ? 'page' : undefined} onClick={() => setOpen(false)}>{link.icon && <svg aria-hidden="true"><use href={link.icon} /></svg>}<span>{link.label}</span></a></Button>)}
        </nav>)}
      </div>
    </SheetContent>
  </Sheet>
}

/** A failed enhancement leaves the original server-rendered controls usable. */
class FallbackBoundary extends React.Component<{children: React.ReactNode; onFailure: () => void}, {failed: boolean}> {
  state = {failed: false}
  static getDerivedStateFromError() { return {failed: true} }
  componentDidCatch(error: Error) { this.props.onFailure(); console.error('UI enhancement failed', error) }
  render() { return this.state.failed ? null : this.props.children }
}

function Enhance({children, ready}: {children: React.ReactNode; ready: () => void}) {
  useEffect(ready, [])
  return children
}

for (const host of document.querySelectorAll<HTMLElement>('[data-ui-account]')) {
  const fallback = [...host.children] as HTMLElement[]
  const links = [...host.querySelectorAll<HTMLAnchorElement>('a[href]')].map(readLink)
  const form = host.querySelector<HTMLFormElement>('form')
  const mount = document.createElement('div')
  mount.className = 'hu-account-mount'
  mount.hidden = true
  host.append(mount)
  // Keep the native form in its original fallback. requestSubmit() works while
  // the details container is hidden, and an enhancement failure restores it intact.
  const restore = () => {fallback.forEach(node => node.hidden = false); mount.hidden = true; delete host.dataset.uiReady}
  try {
    createRoot(mount).render(<FallbackBoundary onFailure={restore}><Enhance ready={() => {fallback.forEach(node => node.hidden = true); mount.hidden = false; host.dataset.uiReady = 'true'}}><AccountMenu host={host} links={links} form={form} /></Enhance></FallbackBoundary>)
  } catch (error) { restore(); console.error('UI enhancement unavailable', error) }
}

const navigationHost = document.querySelector<HTMLElement>('[data-ui-navigation]')
const sidebar = document.querySelector<HTMLElement>('#sidebar')
if (navigationHost && sidebar) {
  createRoot(navigationHost).render(<FallbackBoundary onFailure={() => {
    sidebar.inert = window.matchMedia('(max-width: 1199px)').matches && !sidebar.classList.contains('open')
    delete sidebar.dataset.uiNavigationReady
  }}><NavigationSheet sidebar={sidebar} /></FallbackBoundary>)
}
