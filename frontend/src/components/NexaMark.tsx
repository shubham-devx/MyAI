export function NexaMark({ compact = false }: { compact?: boolean }) {
  return <div className="brand-lockup" aria-label="V-Sutra"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>{!compact && <span className="brand-name">V-<em>Sutra</em></span>}</div>
}
