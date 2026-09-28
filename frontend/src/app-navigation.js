export function canInspectApis(user) {
  return user?.role === 'admin' && Array.isArray(user.terminals)
    && ['cua_lo', 'ben_thuy'].every((terminal) => user.terminals.includes(terminal));
}

export function permittedHash(hash, user) {
  if (hash === '#admin') return user?.role === 'admin' ? hash : '#overview';
  if (hash === '#api-inspector') return canInspectApis(user) ? hash : '#overview';
  return ['#overview', '#production', '#voyages', '#customers', '#data-quality', '#management'].includes(hash) ? hash : '#overview';
}
