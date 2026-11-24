'use client'

import { Check, ChevronRight, MessageSquarePlus, PanelLeftClose, Pencil, Settings2, Sparkles, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import type { Conversation } from '@/lib/types'
import { NexaMark } from './NexaMark'

type Props = {
  collapsed: boolean
  model: string
  conversations: Conversation[]
  activeId: string
  onCollapse: () => void
  onNewChat: () => void
  onSettings: () => void
  onSelectConversation: (id: string) => void
  onRenameConversation: (id: string, title: string) => void
  onDeleteConversation: (id: string) => void
}

export function Sidebar({ collapsed, model, conversations, activeId, onCollapse, onNewChat, onSettings, onSelectConversation, onRenameConversation, onDeleteConversation }: Props) {
  const [editingId, setEditingId] = useState('')
  const [editTitle, setEditTitle] = useState('')
  const [deleteId, setDeleteId] = useState('')

  const beginRename = (conversation: Conversation) => { setEditingId(conversation.id); setEditTitle(conversation.title) }
  const saveRename = (event: React.FormEvent) => {
    event.preventDefault()
    if (editTitle.trim()) onRenameConversation(editingId, editTitle.trim())
    setEditingId('')
  }

  return <aside className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <div className="sidebar-top"><NexaMark compact={collapsed} /><button className="icon-button sidebar-toggle" onClick={onCollapse} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>{collapsed ? <ChevronRight size={17} /> : <PanelLeftClose size={17} />}</button></div>
    <button className="new-chat" onClick={onNewChat}><MessageSquarePlus size={17} /><span>New chat</span></button>
    <div className="sidebar-section conversation-section"><p className="sidebar-label">Temporary conversations</p><nav aria-label="Temporary conversations" className="conversation-nav">
      {conversations.map((conversation) => <div className={`conversation-item ${activeId === conversation.id ? 'active' : ''}`} key={conversation.id}>
        {editingId === conversation.id ? <form className="conversation-rename" onSubmit={saveRename}><input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} autoFocus aria-label="Conversation name" maxLength={80} /><button aria-label="Save name" type="submit"><Check size={14} /></button><button aria-label="Cancel rename" type="button" onClick={() => setEditingId('')}><X size={14} /></button></form> : <>
          <button className="conversation-select" onClick={() => onSelectConversation(conversation.id)} title={conversation.title}><span className="conversation-title">{conversation.title || 'Untitled conversation'}</span><small>{conversation.messages.length} messages</small></button>
          <div className="conversation-actions"><button onClick={() => beginRename(conversation)} aria-label={`Rename ${conversation.title}`} title="Rename"><Pencil size={13} /></button><button onClick={() => setDeleteId(deleteId === conversation.id ? '' : conversation.id)} aria-label={`Delete ${conversation.title}`} title="Delete"><Trash2 size={13} /></button></div>
          {deleteId === conversation.id && <div className="conversation-delete-confirm"><span>Delete this chat?</span><button onClick={() => { onDeleteConversation(conversation.id); setDeleteId('') }}>Delete</button><button onClick={() => setDeleteId('')}>Keep</button></div>}
        </>}
      </div>)}
      {!conversations.length && <p className="sidebar-empty">Your temporary chats will appear here.</p>}
    </nav></div>
    <div className="sidebar-bottom"><div className="model-shortcut"><Sparkles size={16} /><span>{model || 'No model selected'}</span><small>Active</small></div><button className="settings-button" onClick={onSettings}><Settings2 size={17} /><span>Settings</span></button></div>
  </aside>
}
