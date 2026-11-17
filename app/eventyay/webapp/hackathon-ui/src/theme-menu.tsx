import { useEffect, useState } from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'
import { Button } from './components/ui/button'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from './components/ui/dropdown-menu'

// Recover the controller if the early standalone script could not be loaded.
// It is idempotent when the head script has already initialized the appearance.
import '../../../static/hackathon/theme-init.js'

type ThemePreference = 'light' | 'dark' | 'system'
type ThemeState = {preference: ThemePreference; resolved: 'light' | 'dark'}
type ThemeController = {
  getPreference: () => ThemePreference
  getResolved: () => 'light' | 'dark'
  setPreference: (value: ThemePreference) => void
  subscribe: (listener: (state: ThemeState) => void) => () => void
}

declare global { interface Window {rucmakersTheme: ThemeController} }

const labels = {light: '浅色', dark: '深色', system: '跟随系统'}

export function ThemeMenu() {
  const controller = window.rucmakersTheme
  const [theme, setTheme] = useState<ThemeState>(() => ({
    preference: controller.getPreference(), resolved: controller.getResolved(),
  }))
  useEffect(() => {
    const unsubscribe = controller.subscribe(setTheme)
    setTheme({preference: controller.getPreference(), resolved: controller.getResolved()})
    return unsubscribe
  }, [controller])
  const Icon = theme.preference === 'system' ? Monitor : theme.resolved === 'light' ? Sun : Moon
  const description = theme.preference === 'system'
    ? `跟随系统，当前${labels[theme.resolved]}`
    : labels[theme.preference]
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button type="button" variant="ghost" size="icon" className="hu-theme-trigger" aria-label={`外观模式：${description}`} title={`外观模式：${description}`}>
        <Icon aria-hidden="true" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent className={`hu-theme-menu${document.body.classList.contains('entry-page') ? ' hu-entry' : ''}`} align="end" sideOffset={8} collisionPadding={12}>
      <DropdownMenuLabel>外观模式</DropdownMenuLabel>
      <DropdownMenuSeparator />
      <DropdownMenuRadioGroup value={theme.preference} onValueChange={value => controller.setPreference(value as ThemePreference)}>
        <DropdownMenuRadioItem className="hu-item hu-theme-option" value="light"><Sun aria-hidden="true" /><span>浅色</span></DropdownMenuRadioItem>
        <DropdownMenuRadioItem className="hu-item hu-theme-option" value="dark"><Moon aria-hidden="true" /><span>深色</span></DropdownMenuRadioItem>
        <DropdownMenuRadioItem className="hu-item hu-theme-option" value="system"><Monitor aria-hidden="true" /><span>跟随系统</span></DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>
}
