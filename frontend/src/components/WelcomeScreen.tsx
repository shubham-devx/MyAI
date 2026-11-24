import { ArrowUpRight, BookOpen, Braces, FileText, PenLine } from 'lucide-react'
import { suggestionCards } from '@/lib/demo-data'

const icons = [PenLine, BookOpen, Braces, FileText]

export function WelcomeScreen({ onSelect }: { onSelect: (prompt: string) => void }) {
  return <section className="welcome-screen"><div className="welcome-intro"><span className="eyebrow">V-Sutra</span><h1>What can I help you with?</h1><p>Start a thought, get unstuck, or make progress on something that matters.</p></div><div className="suggestion-grid">{suggestionCards.map((card, index) => { const Icon = icons[index]; return <button key={card.title} onClick={() => onSelect(card.prompt)} className="suggestion-card"><span className="suggestion-icon"><Icon size={18} /></span><span className="suggestion-copy"><strong>{card.title}</strong><small>{card.description}</small></span><ArrowUpRight className="suggestion-arrow" size={16} /></button> })}</div><p className="demo-note">Runs locally first — cloud models, web search, and sandboxed code are opt-in.</p></section>
}
