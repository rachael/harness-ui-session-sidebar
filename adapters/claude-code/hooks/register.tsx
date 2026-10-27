// Features, each switched on its own in the plugin's settings (userConfig): the info pane and
// the prompt context drawer (on by default); the status line collector and the session sidebar
// (off by default).
import type { Register } from 'claude-code'

import { registerActivity } from './activity'
import { registerContextDrawer, startContextDrawer } from './drawer'
import { registerInfoPane, startInfoPane } from './info-pane'
import { registerSessionSidebar, startSessionSidebar } from './session-sidebar'
import { registerStatusline, startStatusline } from './statusline'

export const register: Register = (on, options) => {
  const isInfoPane = options.infoPane !== false
  const isDrawer = options.contextDrawer !== false
  const isStatusline = options.statusline === true
  const isSessionSidebar = options.sessionSidebar === true
  // The session says whether it is working or waiting on the person, for any viewer, while a
  // feature of this plugin is on (activity: auto), or as the activity setting says (on, off).
  const isActivity = options.activity === 'on' || (options.activity !== 'off' && (isInfoPane || isDrawer || isStatusline || isSessionSidebar))
  if (isActivity) registerActivity(on)
  if (isInfoPane) registerInfoPane(on)
  if (isDrawer) registerContextDrawer(on)
  if (isStatusline) registerStatusline(on)
  if (isSessionSidebar) registerSessionSidebar(on, options)

  on('session.start', async ($, e, next) => {
    if (isInfoPane) await startInfoPane($).catch(() => undefined)
    if (isDrawer) await startContextDrawer($).catch(() => undefined)
    if (isStatusline) await startStatusline($).catch(() => undefined)
    if (isSessionSidebar) await startSessionSidebar($).catch(() => undefined)

    return next(e)
  })
}
