import { redirect } from 'next/navigation'

/**
 * Root → /voice.
 *
 * The legacy ChatWindow component used to render here, but it has
 * diverged from the maintained chat surface at /voice — no paperclip,
 * no projects/archive, no file upload. Rather than maintain two
 * parallel chat UIs (and add file-vision wiring to both), the root
 * now redirects to /voice. ChatWindow is left in place for one
 * release cycle in case anything else imports it; can be deleted
 * after that.
 */
export default function Home() {
  redirect('/voice')
}
