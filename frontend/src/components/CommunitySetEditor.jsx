import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Copy, Eye, GitCompare, Layers, Pencil, Plus, Save, Search, Trash2 } from 'lucide-react'
import { communitiesApi } from '../api/communities.js'
import { formatAxiosError } from '../api/client.js'
import CommunityApplyConfirmModal from './CommunityApplyConfirmModal.jsx'

const STATUS_BADGE = {
  draft: 'bg-[#252840] text-ink-muted',
  pending_confirmation: 'bg-amber-500/15 text-amber-300 border border-amber-500/25',
  applied: 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/25',
  failed: 'bg-red-500/15 text-red-300 border border-red-500/25',
  read_only: 'bg-slate-500/15 text-slate-300 border border-slate-500/25',
  imported: 'bg-slate-500/15 text-slate-300 border border-slate-500/25',
}

const ORIGIN_BADGE = {
  discovered: 'bg-indigo-500/15 text-indigo-200 border border-indigo-500/25',
  discovered_running_config: 'bg-indigo-500/15 text-indigo-200 border border-indigo-500/25',
  discovered_live: 'bg-sky-500/15 text-sky-200 border border-sky-500/30',
  manual: 'bg-emerald-500/10 text-emerald-200/90 border border-emerald-500/20',
  app_created: 'bg-emerald-500/10 text-emerald-200/90 border border-emerald-500/20',
}

const IMPORTED_SET_ORIGINS = new Set(['discovered', 'discovered_running_config', 'discovered_live'])

function isImportedSetOrigin(origin) {
  return IMPORTED_SET_ORIGINS.has(origin || '')
}

function isAppCreatedOrigin(origin) {
  const o = origin || 'app_created'
  return o === 'app_created' || o === 'manual'
}

export default function CommunitySetEditor({ deviceId, canEdit, canPreview, canApply, onLog }) {
  const latestDeviceId = useRef(deviceId)
  const [sets, setSets] = useState([])
  const [library, setLibrary] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [readOnlyView, setReadOnlyView] = useState(false)
  const [name, setName] = useState('')
  const [vrpName, setVrpName] = useState('')
  const [desc, setDesc] = useState('')
  const [memberIds, setMemberIds] = useState([])
  const [memberSearch, setMemberSearch] = useState('')
  const [saving, setSaving] = useState(false)
  const [applyModal, setApplyModal] = useState({ open: false, setId: null, preview: null })
  const [compareModal, setCompareModal] = useState({ open: false, baseId: null, otherId: '', result: null })

  const editingSet = useMemo(() => sets.find((x) => x.id === editingId) || null, [sets, editingId])

  const filteredLibrary = useMemo(() => {
    const q = memberSearch.trim().toLowerCase()
    if (!q) return library
    return library.filter((it) => {
      const blob = [it.filter_name, it.name, it.community_value, it.description, it.match_type, it.origin]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return blob.includes(q)
    })
  }, [library, memberSearch])

  const loadAll = useCallback(async () => {
    const targetId = deviceId
    setLoading(true)
    setErr('')
    try {
      const [s, lib] = await Promise.all([communitiesApi.listSets(targetId), communitiesApi.library(targetId)])
      if (latestDeviceId.current !== targetId) return
      const setList = Array.isArray(s) ? s : []
      const libList = Array.isArray(lib) ? lib : []
      setSets(setList.filter((x) => x.device_id === targetId))
      setLibrary(libList.filter((x) => x.device_id === targetId))
    } catch (e) {
      if (latestDeviceId.current === targetId) setErr(formatAxiosError(e))
    } finally {
      if (latestDeviceId.current === targetId) setLoading(false)
    }
  }, [deviceId])

  useEffect(() => {
    latestDeviceId.current = deviceId
  }, [deviceId])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  function resetForm() {
    setEditingId(null)
    setReadOnlyView(false)
    setName('')
    setVrpName('')
    setDesc('')
    setMemberIds([])
    setMemberSearch('')
  }

  function startCreate() {
    resetForm()
    setReadOnlyView(false)
    setEditingId('new')
  }

  function openSet(s) {
    setMemberSearch('')
    setEditingId(s.id)
    setName(s.name || '')
    setVrpName(s.vrp_object_name || '')
    setDesc(s.description || '')
    const isDisc = isImportedSetOrigin(s.origin)
    setReadOnlyView(isDisc || !canEdit)
    if (!isDisc && canEdit) {
      setMemberIds(
        (s.members || [])
          .slice()
          .sort((a, b) => a.position - b.position)
          .map((m) => m.linked_library_item_id)
          .filter((id) => id != null),
      )
    } else {
      setMemberIds([])
    }
  }

  function toggleMember(id) {
    setMemberIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  async function save() {
    if (!canEdit || editingId === null || editingId === 'new' || readOnlyView) return
    setSaving(true)
    setErr('')
    try {
      await communitiesApi.updateSet(deviceId, editingId, {
        name,
        vrp_object_name: vrpName || undefined,
        description: desc || undefined,
        member_library_item_ids: memberIds,
      })
      onLog?.('success', 'Community set atualizado.')
      resetForm()
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setSaving(false)
    }
  }

  async function saveAndPreview() {
    if (!canEdit || !canPreview || editingId === null || editingId === 'new' || readOnlyView) return
    setSaving(true)
    setErr('')
    try {
      const updated = await communitiesApi.updateSet(deviceId, editingId, {
        name,
        vrp_object_name: vrpName || undefined,
        description: desc || undefined,
        member_library_item_ids: memberIds,
      })
      const preview = await communitiesApi.previewSet(deviceId, updated.id)
      onLog?.('success', 'Community set atualizado e preparado para confirmação de aplicação.')
      setApplyModal({ open: true, setId: updated.id, preview })
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setSaving(false)
    }
  }

  async function saveNew() {
    if (!canEdit || editingId !== 'new') return
    setSaving(true)
    setErr('')
    try {
      await communitiesApi.createSet(deviceId, {
        name,
        vrp_object_name: vrpName || undefined,
        description: desc || undefined,
        member_library_item_ids: memberIds,
      })
      onLog?.('success', 'Community set criado (rascunho).')
      resetForm()
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setSaving(false)
    }
  }

  async function saveNewAndPreview() {
    if (!canEdit || !canPreview || editingId !== 'new') return
    setSaving(true)
    setErr('')
    try {
      const created = await communitiesApi.createSet(deviceId, {
        name,
        vrp_object_name: vrpName || undefined,
        description: desc || undefined,
        member_library_item_ids: memberIds,
      })
      const preview = await communitiesApi.previewSet(deviceId, created.id)
      onLog?.('success', 'Community set criado e preparado para confirmação de aplicação.')
      setApplyModal({ open: true, setId: created.id, preview })
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setSaving(false)
    }
  }

  async function removeSet(id) {
    if (!canEdit) return
    if (!window.confirm('Apagar este community set?')) return
    setErr('')
    try {
      await communitiesApi.deleteSet(deviceId, id)
      onLog?.('info', 'Community set removido.')
      if (editingId === id) resetForm()
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    }
  }

  async function runClone(id) {
    if (!canEdit) return
    setErr('')
    try {
      await communitiesApi.cloneSet(deviceId, id, {})
      onLog?.('success', 'Clone criado como rascunho (app_created).')
      resetForm()
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    }
  }

  async function runCompare() {
    const a = compareModal.baseId
    const b = parseInt(compareModal.otherId, 10)
    if (a == null || Number.isNaN(b) || a === b) {
      setErr('Selecione outro set válido para comparar.')
      return
    }
    setErr('')
    try {
      const result = await communitiesApi.compareSets(deviceId, { set_id_a: a, set_id_b: b })
      setCompareModal((m) => ({ ...m, result }))
    } catch (e) {
      setErr(formatAxiosError(e))
    }
  }

  async function runPreview(setId) {
    if (!canPreview) return
    setErr('')
    try {
      const preview = await communitiesApi.previewSet(deviceId, setId)
      setApplyModal({ open: true, setId, preview })
      await loadAll()
    } catch (e) {
      setErr(formatAxiosError(e))
    }
  }

  const showRightPanel = editingId !== null
  const isNew = editingId === 'new'
  const manualEditable = showRightPanel && !readOnlyView && !isNew && canEdit

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-ink-muted leading-relaxed max-w-[52rem]">
          <span className="font-mono text-ink-secondary">ip community-list</span> importado aparece como set com origem{' '}
          <strong className="text-ink-secondary">discovered_*</strong> (só leitura; clone para editar). Sets{' '}
          <strong className="text-ink-secondary">app_created</strong> são rascunhos da app; membros referenciam a
          biblioteca (<span className="font-mono text-ink-secondary">community-filter</span> e valores{' '}
          <span className="font-mono text-ink-secondary">derived</span>).
        </p>
        {canEdit && (
          <button
            type="button"
            onClick={startCreate}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-brand-blue text-white text-[12px] hover:bg-brand-blue-hover"
          >
            <Plus size={14} />
            Novo set (app)
          </button>
        )}
      </div>

      {err && (
        <div className="text-[12px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{err}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-[#252840] overflow-hidden flex flex-col min-h-[280px]">
          <div className="px-3 py-2 bg-[#161922] text-[11px] font-semibold text-ink-muted uppercase tracking-wide">
            Community sets neste dispositivo
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {loading ? (
              <p className="text-[12px] text-ink-muted p-3">A carregar…</p>
            ) : sets.length === 0 ? (
              <p className="text-[12px] text-ink-muted p-3">
                Nenhum set. Sincronize na aba Biblioteca para importar listas do running-config.
              </p>
            ) : (
              sets.map((s) => (
                <div
                  key={s.id}
                  className={[
                    'rounded-lg border px-3 py-2 cursor-pointer transition-colors',
                    editingId === s.id ? 'border-brand-blue bg-brand-blue-dim' : 'border-[#252840] hover:bg-[#1a1d2e]',
                  ].join(' ')}
                  onClick={() => openSet(s)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-[13px] font-semibold text-ink-primary truncate">{s.name}</p>
                      <p className="text-[10px] font-mono text-ink-muted truncate">{s.vrp_object_name}</p>
                      <p className="text-[9px] text-ink-muted mt-0.5">
                        membros: {s.members_total ?? (s.members || []).length}
                        {typeof s.members_resolved === 'number' ? (
                          <>
                            {' · '}
                            <span className="text-emerald-400/80">ok {s.members_resolved}</span>
                            {s.members_missing > 0 ? (
                              <span className="text-amber-300/90"> · ausentes {s.members_missing}</span>
                            ) : null}
                          </>
                        ) : null}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span
                        className={[
                          'text-[8px] uppercase px-1.5 py-0.5 rounded border',
                          ORIGIN_BADGE[s.origin] || ORIGIN_BADGE.app_created,
                        ].join(' ')}
                      >
                        {s.origin || 'app_created'}
                      </span>
                      <span
                        className={[
                          'text-[9px] uppercase px-1.5 py-0.5 rounded border',
                          STATUS_BADGE[s.status] || STATUS_BADGE.draft,
                        ].join(' ')}
                      >
                        {s.status}
                      </span>
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {canEdit && isImportedSetOrigin(s.origin) && (
                      <button
                        type="button"
                        className="text-[10px] px-2 py-1 rounded border border-[#252840] text-ink-muted hover:text-ink-primary inline-flex items-center gap-1"
                        onClick={(e) => {
                          e.stopPropagation()
                          openSet(s)
                        }}
                      >
                        Detalhe
                      </button>
                    )}
                    {canEdit && isImportedSetOrigin(s.origin) && (
                      <button
                        type="button"
                        className="text-[10px] px-2 py-1 rounded border border-brand-blue/40 text-brand-blue hover:bg-brand-blue/10 inline-flex items-center gap-1"
                        onClick={(e) => {
                          e.stopPropagation()
                          runClone(s.id)
                        }}
                      >
                        <Copy size={11} /> Clonar
                      </button>
                    )}
                    {canEdit && isImportedSetOrigin(s.origin) && (
                      <button
                        type="button"
                        className="text-[10px] px-2 py-1 rounded border border-[#252840] text-ink-muted hover:text-amber-200 inline-flex items-center gap-1"
                        onClick={(e) => {
                          e.stopPropagation()
                          openSet(s)
                          setCompareModal({ open: true, baseId: s.id, otherId: '', result: null })
                        }}
                      >
                        <GitCompare size={11} /> Comparar
                      </button>
                    )}
                    {canEdit && isAppCreatedOrigin(s.origin) && (
                      <button
                        type="button"
                        title="Editar set"
                        aria-label="Editar set"
                        className="text-[10px] px-2 py-1 rounded border border-[#252840] text-ink-muted hover:text-ink-primary inline-flex items-center"
                        onClick={(e) => {
                          e.stopPropagation()
                          openSet(s)
                        }}
                      >
                        <Pencil size={12} />
                      </button>
                    )}
                    {canPreview &&
                      isAppCreatedOrigin(s.origin) &&
                      ['draft', 'failed', 'pending_confirmation'].includes(s.status) && (
                        <button
                          type="button"
                          className="text-[10px] px-2 py-1 rounded border border-[#252840] text-ink-muted hover:text-brand-blue inline-flex items-center gap-1"
                          onClick={(e) => {
                            e.stopPropagation()
                            runPreview(s.id)
                          }}
                        >
                          <Eye size={11} /> Preview
                        </button>
                      )}
                    {canEdit &&
                      isAppCreatedOrigin(s.origin) &&
                      ['draft', 'failed', 'pending_confirmation'].includes(s.status) && (
                        <button
                          type="button"
                          className="text-[10px] px-2 py-1 rounded border border-red-500/30 text-red-400 hover:bg-red-500/10 inline-flex items-center gap-1"
                          onClick={(e) => {
                            e.stopPropagation()
                            removeSet(s.id)
                          }}
                        >
                          <Trash2 size={11} /> Apagar
                        </button>
                      )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {showRightPanel && (
          <div className="rounded-lg border border-[#252840] p-4 flex flex-col gap-3">
            {isNew ? (
              <>
                <div className="flex items-center gap-2 text-ink-primary text-[13px] font-semibold">
                  <Layers size={15} />
                  Novo community set (app)
                </div>
                <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
                  Nome amigável
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={!canEdit}
                    className="px-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] text-ink-primary disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
                  Nome objeto VRP (ip community-list)
                  <input
                    value={vrpName}
                    onChange={(e) => setVrpName(e.target.value)}
                    disabled={!canEdit}
                    className="px-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] font-mono text-ink-primary disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
                  Descrição (opcional)
                  <input
                    value={desc}
                    onChange={(e) => setDesc(e.target.value)}
                    disabled={!canEdit}
                    className="px-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] text-ink-primary disabled:opacity-50"
                  />
                </label>
                <div className="flex flex-col gap-2">
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-muted">Communities da biblioteca (multi-seleção)</span>
                    <div className="relative">
                      <Search
                        size={14}
                        className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted pointer-events-none"
                      />
                      <input
                        type="search"
                        value={memberSearch}
                        onChange={(e) => setMemberSearch(e.target.value)}
                        placeholder="Filtrar por nome, valor (ex. 65001:100) ou descrição…"
                        disabled={!library.length}
                        className="w-full pl-8 pr-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[12px] text-ink-primary placeholder:text-ink-muted focus:border-brand-blue outline-none disabled:opacity-50"
                      />
                    </div>
                  </div>
                  <div className="max-h-48 overflow-y-auto rounded-lg border border-[#252840] divide-y divide-[#252840]">
                    {library.length === 0 ? (
                      <p className="text-[11px] text-ink-muted p-2">Biblioteca vazia — sincronize na aba Biblioteca.</p>
                    ) : filteredLibrary.length === 0 ? (
                      <p className="text-[11px] text-ink-muted p-2">Nenhum resultado para esta pesquisa.</p>
                    ) : (
                      filteredLibrary.map((it) => (
                        <label
                          key={it.id}
                          className="flex items-start gap-2 px-2 py-1.5 hover:bg-[#1a1d2e] cursor-pointer text-[11px]"
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5 shrink-0"
                            checked={memberIds.includes(it.id)}
                            onChange={() => toggleMember(it.id)}
                            disabled={!canEdit}
                          />
                          <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0">
                              <span className="font-mono text-ink-secondary truncate">{it.filter_name ?? it.name}</span>
                              <span className="font-mono text-brand-blue/90 truncate">{it.community_value}</span>
                            </div>
                            {it.description ? (
                              <span className="text-[10px] text-ink-muted leading-snug line-clamp-2">{it.description}</span>
                            ) : null}
                          </div>
                        </label>
                      ))
                    )}
                  </div>
                </div>
                {canEdit && (
                  <div className="flex gap-2 pt-2">
                    <button
                      type="button"
                      disabled={saving || !name.trim()}
                      onClick={saveNew}
                      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-brand-blue text-white text-[12px] disabled:opacity-40"
                    >
                      <Save size={14} />
                      Guardar
                    </button>
                    {canPreview && (
                      <button
                        type="button"
                        disabled={saving || !name.trim()}
                        onClick={saveNewAndPreview}
                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-brand-blue/40 text-brand-blue text-[12px] hover:bg-brand-blue/10 disabled:opacity-40"
                      >
                        <Eye size={14} />
                        Guardar e aplicar…
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={resetForm}
                      className="px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-muted"
                    >
                      Cancelar
                    </button>
                  </div>
                )}
              </>
            ) : readOnlyView && editingSet ? (
              <>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-ink-primary text-[13px] font-semibold">
                    <Layers size={15} />
                    {isImportedSetOrigin(editingSet.origin)
                      ? 'Set importado (VRP)'
                      : 'Visualização'}
                  </div>
                  <span
                    className={[
                      'text-[8px] uppercase px-1.5 py-0.5 rounded border',
                      ORIGIN_BADGE[editingSet.origin] || ORIGIN_BADGE.app_created,
                    ].join(' ')}
                  >
                    {editingSet.origin || 'app_created'}
                  </span>
                </div>
                <div className="text-[11px] text-ink-muted space-y-1">
                  <p>
                    <span className="text-ink-muted">Nome:</span>{' '}
                    <span className="text-ink-primary">{editingSet.name}</span>
                  </p>
                  <p className="font-mono text-ink-secondary text-[12px]">{editingSet.vrp_object_name}</p>
                  {editingSet.description ? <p className="text-[10px]">{editingSet.description}</p> : null}
                </div>
                <div>
                  <p className="text-[11px] text-ink-muted mb-1">Membros ({(editingSet.members || []).length})</p>
                  <ul className="max-h-40 overflow-y-auto rounded-lg border border-[#252840] divide-y divide-[#252840] text-[11px] font-mono text-ink-secondary">
                    {(editingSet.members || []).map((m, idx) => (
                      <li key={`${m.community_value}-${idx}`} className="px-2 py-1">
                        {m.community_value}
                        {m.linked_filter_name ? (
                          <span className="text-ink-muted text-[10px] ml-2">({m.linked_filter_name})</span>
                        ) : null}
                        {m.missing_in_library ? (
                          <span className="text-amber-400/90 text-[9px] ml-2 uppercase">ausente na biblioteca</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
                {editingSet.implied_config_preview ? (
                  <div>
                    <p className="text-[11px] text-ink-muted mb-1">Bloco VRP equivalente</p>
                    <pre className="text-[10px] font-mono text-ink-secondary bg-[#12141c] border border-[#252840] rounded-lg p-2 overflow-x-auto whitespace-pre-wrap">
                      {editingSet.implied_config_preview}
                    </pre>
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-2 pt-2">
                  {canEdit && isImportedSetOrigin(editingSet.origin) && (
                    <button
                      type="button"
                      onClick={() => runClone(editingSet.id)}
                      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-brand-blue/40 text-brand-blue text-[12px] hover:bg-brand-blue/10"
                    >
                      <Copy size={14} /> Clonar para rascunho (app)
                    </button>
                  )}
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() =>
                        setCompareModal({ open: true, baseId: editingId, otherId: '', result: null })
                      }
                      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-muted hover:text-amber-200"
                    >
                      <GitCompare size={14} /> Comparar com outro set
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={resetForm}
                    className="px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-muted"
                  >
                    Fechar
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 text-ink-primary text-[13px] font-semibold">
                  <Layers size={15} />
                  Editar community set (app)
                </div>
                <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
                  Nome amigável
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={!canEdit}
                    className="px-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] text-ink-primary disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
                  Nome objeto VRP (ip community-list)
                  <input
                    value={vrpName}
                    onChange={(e) => setVrpName(e.target.value)}
                    disabled={!canEdit}
                    className="px-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] font-mono text-ink-primary disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
                  Descrição (opcional)
                  <input
                    value={desc}
                    onChange={(e) => setDesc(e.target.value)}
                    disabled={!canEdit}
                    className="px-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] text-ink-primary disabled:opacity-50"
                  />
                </label>
                <div className="flex flex-col gap-2">
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-muted">Communities da biblioteca (multi-seleção)</span>
                    <div className="relative">
                      <Search
                        size={14}
                        className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted pointer-events-none"
                      />
                      <input
                        type="search"
                        value={memberSearch}
                        onChange={(e) => setMemberSearch(e.target.value)}
                        placeholder="Filtrar por nome, valor (ex. 65001:100) ou descrição…"
                        disabled={!library.length}
                        className="w-full pl-8 pr-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[12px] text-ink-primary placeholder:text-ink-muted focus:border-brand-blue outline-none disabled:opacity-50"
                      />
                    </div>
                  </div>
                  <div className="max-h-48 overflow-y-auto rounded-lg border border-[#252840] divide-y divide-[#252840]">
                    {library.length === 0 ? (
                      <p className="text-[11px] text-ink-muted p-2">Biblioteca vazia — sincronize na aba Biblioteca.</p>
                    ) : filteredLibrary.length === 0 ? (
                      <p className="text-[11px] text-ink-muted p-2">Nenhum resultado para esta pesquisa.</p>
                    ) : (
                      filteredLibrary.map((it) => (
                        <label
                          key={it.id}
                          className="flex items-start gap-2 px-2 py-1.5 hover:bg-[#1a1d2e] cursor-pointer text-[11px]"
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5 shrink-0"
                            checked={memberIds.includes(it.id)}
                            onChange={() => toggleMember(it.id)}
                            disabled={!canEdit}
                          />
                          <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0">
                              <span className="font-mono text-ink-secondary truncate">{it.filter_name ?? it.name}</span>
                              <span className="font-mono text-brand-blue/90 truncate">{it.community_value}</span>
                            </div>
                            {it.description ? (
                              <span className="text-[10px] text-ink-muted leading-snug line-clamp-2">{it.description}</span>
                            ) : null}
                          </div>
                        </label>
                      ))
                    )}
                  </div>
                </div>
                {manualEditable && (
                  <div className="flex gap-2 pt-2">
                    <button
                      type="button"
                      disabled={saving || !name.trim()}
                      onClick={save}
                      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-brand-blue text-white text-[12px] disabled:opacity-40"
                    >
                      <Save size={14} />
                      Guardar
                    </button>
                    {canPreview && (
                      <button
                        type="button"
                        disabled={saving || !name.trim()}
                        onClick={saveAndPreview}
                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-brand-blue/40 text-brand-blue text-[12px] hover:bg-brand-blue/10 disabled:opacity-40"
                      >
                        <Eye size={14} />
                        Guardar e aplicar…
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={resetForm}
                      className="px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-muted"
                    >
                      Cancelar
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {compareModal.open && compareModal.baseId != null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="dialog">
          <div className="w-full max-w-lg rounded-xl border border-[#252840] bg-[#161922] p-4 shadow-xl">
            <h3 className="text-[14px] font-semibold text-ink-primary mb-2 flex items-center gap-2">
              <GitCompare size={16} /> Comparar sets
            </h3>
            <p className="text-[11px] text-ink-muted mb-3">
              Set A:{' '}
              <span className="font-mono text-ink-secondary">
                {sets.find((x) => x.id === compareModal.baseId)?.name || `#${compareModal.baseId}`}
              </span>{' '}
              (id {compareModal.baseId})
            </p>
            <label className="flex flex-col gap-1 text-[11px] text-ink-muted mb-3">
              Set B (mesmo dispositivo)
              <select
                value={compareModal.otherId}
                onChange={(e) => setCompareModal((m) => ({ ...m, otherId: e.target.value, result: null }))}
                className="px-3 py-2 rounded-lg bg-[#12141c] border border-[#252840] text-[12px] text-ink-primary"
              >
                <option value="">— escolher —</option>
                {sets
                  .filter((x) => x.id !== compareModal.baseId)
                  .map((x) => (
                    <option key={x.id} value={String(x.id)}>
                      {x.filter_name ?? x.name} ({x.origin || 'app_created'})
                    </option>
                  ))}
              </select>
            </label>
            <div className="flex gap-2 mb-3">
              <button
                type="button"
                onClick={runCompare}
                className="px-3 py-2 rounded-lg bg-brand-blue text-white text-[12px]"
              >
                Executar comparação
              </button>
              <button
                type="button"
                onClick={() => setCompareModal({ open: false, baseId: null, otherId: '', result: null })}
                className="px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-muted"
              >
                Fechar
              </button>
            </div>
            {compareModal.result && (
              <div className="text-[11px] space-y-2 border-t border-[#252840] pt-3 text-ink-muted">
                <p>
                  <span className="text-ink-secondary">{compareModal.result.set_a_name}</span> —{' '}
                  {compareModal.result.members_a_sorted?.length ?? 0} membros
                </p>
                <p>
                  <span className="text-ink-secondary">{compareModal.result.set_b_name}</span> —{' '}
                  {compareModal.result.members_b_sorted?.length ?? 0} membros
                </p>
                <p className="text-emerald-300/90">Em ambos ({compareModal.result.in_both?.length ?? 0})</p>
                <pre className="text-[10px] font-mono bg-[#12141c] rounded p-2 max-h-24 overflow-y-auto">
                  {(compareModal.result.in_both || []).join('\n') || '—'}
                </pre>
                <p className="text-amber-200/90">Só em A ({compareModal.result.only_in_a?.length ?? 0})</p>
                <pre className="text-[10px] font-mono bg-[#12141c] rounded p-2 max-h-24 overflow-y-auto">
                  {(compareModal.result.only_in_a || []).join('\n') || '—'}
                </pre>
                <p className="text-amber-200/90">Só em B ({compareModal.result.only_in_b?.length ?? 0})</p>
                <pre className="text-[10px] font-mono bg-[#12141c] rounded p-2 max-h-24 overflow-y-auto">
                  {(compareModal.result.only_in_b || []).join('\n') || '—'}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      <CommunityApplyConfirmModal
        open={applyModal.open}
        preview={applyModal.preview}
        deviceId={deviceId}
        setId={applyModal.setId}
        canApply={canApply}
        onClose={() => setApplyModal({ open: false, setId: null, preview: null })}
        onApplied={() => {
          onLog?.('success', 'Community set aplicado no roteador.')
          loadAll()
        }}
      />
    </div>
  )
}
