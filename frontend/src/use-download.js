import { useEffect, useRef, useState } from 'react';
import { downloadFile } from './api-client.js';

export default function useDownload() {
  const [busy, setBusy] = useState(false);
  const pending = useRef(null);
  useEffect(() => () => { pending.current?.abort(); pending.current = null; }, []);
  async function run(path, filename, options) {
    if (pending.current) return { skipped: true };
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    try {
      await downloadFile(path, filename, { ...options, signal: controller.signal });
      return { ok: true };
    } catch (error) {
      return controller.signal.aborted ? { cancelled: true } : { error };
    } finally {
      if (pending.current === controller) { pending.current = null; setBusy(false); }
    }
  }
  return { busy, run };
}
