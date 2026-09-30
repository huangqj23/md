import ai from './ai'
import aiDaily from './aiDaily'
import chrome from './chrome'
import common from './common'
import dialog from './dialog'
import editor from './editor'
import marketplace from './marketplace'
import notifications from './notifications'
import publish from './publish'
import store from './store'
import upload from './upload'

export default {
  ...common,
  ...chrome,
  ...dialog,
  ...editor,
  ...marketplace,
  ...notifications,
  ...publish,
  ...aiDaily,
  ...ai,
  ...upload,
  ...store,
}
